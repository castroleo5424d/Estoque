# language: Python, file: app.py
from flask import Flask, render_template, request, redirect, url_for, flash, Response
from db import get_conn, init_db
import queue
import json

app = Flask(__name__)
app.secret_key = "troque-em-producao"
_listeners = []

@app.context_processor
def inject_alerts():
    conn = get_conn()
    try:
        alertas_count = conn.execute("""
            SELECT COUNT(*) as c FROM produtos p 
            WHERE COALESCE((SELECT SUM(quantidade) FROM estoque_posicoes ep WHERE ep.produto_id = p.id), 0) <= p.estoque_minimo
        """).fetchone()["c"]
        return dict(alertas_count=alertas_count)
    except Exception:
        return dict(alertas_count=0)
    finally:
        conn.close()

def notify(event_type, payload=None):
    msg = json.dumps({"type": event_type, "data": payload or {}})
    for q in list(_listeners):
        try:
            q.put_nowait(msg)
        except queue.Full:
            pass

@app.route("/stream")
def stream():
    def event_stream():
        q = queue.Queue(maxsize=10)
        _listeners.append(q)
        try:
            yield "data: {\"type\":\"ping\"}\n\n"
            while True:
                msg = q.get()
                yield f"data: {msg}\n\n"
        except GeneratorExit:
            _listeners.remove(q)
    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.template_filter("brl")
def brl(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

@app.route("/", methods=["GET", "POST"])
def dashboard():
    conn = get_conn()
    if request.method == "POST":
        acao = request.form.get("acao")
        pid = int(request.form["produto_id"])
        try:
            if acao == "transferir":
                origem = int(request.form["origem_id"])
                destino = int(request.form["destino_id"])
                qtd = int(request.form["quantidade"])
                
                if origem == destino:
                    raise ValueError("Origem e destino são iguais.")
                if qtd <= 0:
                    raise ValueError("Quantidade deve ser maior que zero.")

                lote_origem = conn.execute("SELECT quantidade FROM estoque_posicoes WHERE produto_id = ? AND posicao_id = ?", (pid, origem)).fetchone()
                if not lote_origem or lote_origem["quantidade"] < qtd:
                    raise ValueError("Saldo insuficiente na posição de origem.")

                conn.execute("UPDATE estoque_posicoes SET quantidade = quantidade - ? WHERE produto_id = ? AND posicao_id = ?", (qtd, pid, origem))
                conn.execute("""
                    INSERT INTO estoque_posicoes (produto_id, posicao_id, quantidade) 
                    VALUES (?, ?, ?)
                    ON CONFLICT(produto_id, posicao_id) DO UPDATE SET quantidade = quantidade + excluded.quantidade
                """, (pid, destino, qtd))
                
                conn.execute("DELETE FROM estoque_posicoes WHERE produto_id = ? AND quantidade <= 0", (pid,))
                conn.execute("INSERT INTO transferencias (produto_id, posicao_origem_id, posicao_destino_id, quantidade) VALUES (?,?,?,?)", (pid, origem, destino, qtd))
                
                conn.commit()
                notify("mudanca")
                flash("Transferência realizada.", "ok")

        except Exception as e:
            conn.rollback()
            flash(f"Erro: {e}", "err")
        finally:
            conn.close()
        return redirect(url_for("dashboard", q=request.form.get("busca_atual", "")))

    busca = request.args.get("q", "").strip()
    resultados = []
    posicoes_lista = conn.execute("SELECT * FROM posicoes ORDER BY corredor, prateleira").fetchall()
    
    if busca:
        prods = conn.execute(
            """SELECT p.*, 
                      COALESCE((SELECT SUM(quantidade) FROM estoque_posicoes ep WHERE ep.produto_id = p.id), 0) as total
               FROM produtos p 
               WHERE p.nome LIKE ? OR p.sku LIKE ? ORDER BY p.nome""",
            (f"%{busca}%", f"%{busca}%")
        ).fetchall()
        
        resultados = []
        for p in prods:
            lotes = conn.execute("""
                SELECT ep.quantidade, pos.id, pos.corredor, pos.prateleira 
                FROM estoque_posicoes ep
                JOIN posicoes pos ON pos.id = ep.posicao_id
                WHERE ep.produto_id = ? AND ep.quantidade > 0
            """, (p["id"],)).fetchall()
            
            d = dict(p)
            d["lotes"] = lotes
            d["total"] = p["total"]
            resultados.append(d)

    conn.close()
    return render_template("dashboard.html", busca=busca, resultados=resultados, posicoes=posicoes_lista)

@app.route("/posicoes", methods=["GET", "POST"])
def posicoes():
    conn = get_conn()
    if request.method == "POST":
        try:
            conn.execute(
                "INSERT INTO posicoes (corredor, prateleira, descricao) VALUES (?,?,?)",
                (request.form["corredor"].strip(), request.form["prateleira"].strip(), request.form["descricao"].strip())
            )
            conn.commit()
            notify("mudanca")
            flash("Posição cadastrada.", "ok")
        except Exception as e:
            flash(f"Erro: {e}", "err")
        finally:
            conn.close()
        return redirect(url_for("posicoes"))
    
    posicoes_raw = conn.execute("SELECT * FROM posicoes ORDER BY corredor, prateleira").fetchall()
    
    posicoes = []
    for pos in posicoes_raw:
        itens = conn.execute("""
            SELECT p.id as produto_id, p.nome, p.sku, ep.quantidade
            FROM estoque_posicoes ep
            JOIN produtos p ON p.id = ep.produto_id
            WHERE ep.posicao_id = ? AND ep.quantidade > 0
        """, (pos["id"],)).fetchall()
        
        d = dict(pos)
        d["itens"] = [dict(i) for i in itens]
        posicoes.append(d)

    conn.close()
    return render_template("posicoes.html", posicoes=posicoes)

@app.route("/posicoes/<int:pos_id>/gerenciar", methods=["POST"])
def gerenciar_posicao(pos_id):
    conn = get_conn()
    acao = request.form.get("acao")
    
    try:
        if acao == "transferir":
            produto_id = int(request.form["produto_id"])
            destino_id = int(request.form["destino_id"])
            qtd = int(request.form["quantidade"])
            
            if pos_id == destino_id:
                raise ValueError("O destino não pode ser a própria posição.")
            if qtd <= 0:
                raise ValueError("A quantidade deve ser maior que zero.")

            lote = conn.execute("SELECT quantidade FROM estoque_posicoes WHERE produto_id = ? AND posicao_id = ?", (produto_id, pos_id)).fetchone()
            if not lote or lote["quantidade"] < qtd:
                raise ValueError("Quantidade insuficiente neste lote.")

            conn.execute("UPDATE estoque_posicoes SET quantidade = quantidade - ? WHERE produto_id = ? AND posicao_id = ?", (qtd, produto_id, pos_id))
            conn.execute("""
                INSERT INTO estoque_posicoes (produto_id, posicao_id, quantidade) 
                VALUES (?, ?, ?)
                ON CONFLICT(produto_id, posicao_id) DO UPDATE SET quantidade = quantidade + excluded.quantidade
            """, (produto_id, destino_id, qtd))
            conn.execute("DELETE FROM estoque_posicoes WHERE produto_id = ? AND posicao_id = ? AND quantidade <= 0", (produto_id, pos_id))
            conn.execute("INSERT INTO transferencias (produto_id, posicao_origem_id, posicao_destino_id, quantidade) VALUES (?,?,?,?)", (produto_id, pos_id, destino_id, qtd))
            flash("Produto transferido com sucesso.", "ok")

        elif acao == "excluir":
            saldo_total = conn.execute("SELECT SUM(quantidade) FROM estoque_posicoes WHERE posicao_id = ?", (pos_id,)).fetchone()[0]
            if saldo_total and saldo_total > 0:
                raise ValueError("A posição não está vazia. Transfira todos os produtos antes de excluir.")
            
            conn.execute("DELETE FROM estoque_posicoes WHERE posicao_id = ?", (pos_id,))
            conn.execute("DELETE FROM posicoes WHERE id = ?", (pos_id,))
            flash("Posição excluída com sucesso.", "ok")

        conn.commit()
        notify("mudanca")
    except Exception as e:
        conn.rollback()
        flash(f"Erro: {e}", "err")
    finally:
        conn.close()
    return redirect(url_for("posicoes"))

@app.route("/produtos", methods=["GET", "POST"])
def produtos():
    conn = get_conn()
    if request.method == "POST":
        try:
            sku = request.form["sku"].strip()
            nome = request.form["nome"].strip()
            cat = request.form["categoria"].strip()
            preco = float(request.form["preco"])
            est_min = int(request.form["estoque_minimo"])
            
            cursor = conn.execute(
                "INSERT INTO produtos (sku, nome, categoria, preco, estoque_minimo) VALUES (?,?,?,?,?)",
                (sku, nome, cat, preco, est_min)
            )
            new_id = cursor.lastrowid
            
            pos_raw = request.form.get("posicao_id")
            qtd_raw = request.form.get("quantidade", "0")
            
            if pos_raw and qtd_raw:
                pos_id = int(pos_raw)
                qtd_init = int(qtd_raw)
                
                if qtd_init > 0:
                    if conn.execute("SELECT id FROM posicoes WHERE id = ?", (pos_id,)).fetchone():
                        conn.execute("INSERT INTO estoque_posicoes (produto_id, posicao_id, quantidade) VALUES (?,?,?)", (new_id, pos_id, qtd_init))
                        conn.execute("INSERT INTO movimentacoes (produto_id, tipo, quantidade, observacao) VALUES (?,?,?,?)", (new_id, "entrada", qtd_init, "Estoque inicial no cadastro"))

            conn.commit()
            notify("mudanca")
            flash("Produto cadastrado.", "ok")
        except Exception as e:
            conn.rollback()
            flash(f"Erro: {e}", "err")
        finally:
            conn.close()
        return redirect(url_for("produtos"))

    busca = request.args.get("q", "").strip()
    query_base = """
        SELECT p.*, 
               COALESCE((SELECT SUM(quantidade) FROM estoque_posicoes ep WHERE ep.produto_id = p.id), 0) as total
        FROM produtos p
    """
    if busca:
        prods_raw = conn.execute(
            query_base + " WHERE p.nome LIKE ? OR p.sku LIKE ? OR p.categoria LIKE ? ORDER BY p.nome",
            (f"%{busca}%", f"%{busca}%", f"%{busca}%")
        ).fetchall()
    else:
        prods_raw = conn.execute(query_base + " ORDER BY p.nome").fetchall()
        
    produtos = []
    for p in prods_raw:
        lotes = conn.execute("""
            SELECT pos.id, pos.corredor, pos.prateleira, ep.quantidade 
            FROM estoque_posicoes ep
            JOIN posicoes pos ON pos.id = ep.posicao_id
            WHERE ep.produto_id = ? AND ep.quantidade > 0
        """, (p["id"],)).fetchall()
        
        d = dict(p)
        d["lotes"] = [dict(l) for l in lotes]
        produtos.append(d)
        
    posicoes_lista = conn.execute("SELECT * FROM posicoes ORDER BY corredor, prateleira").fetchall()
    conn.close()
    return render_template("produtos.html", produtos=produtos, busca=busca, posicoes=posicoes_lista)

@app.route("/produtos/<int:pid>/editar", methods=["POST"])
def editar_produto(pid):
    conn = get_conn()
    try:
        posicao_id = int(request.form["posicao_id"])
        quantidade = int(request.form["quantidade"])
        acao = request.form["acao"]

        if quantidade <= 0:
            raise ValueError("A quantidade deve ser maior que zero.")

        lote = conn.execute("SELECT id, quantidade FROM estoque_posicoes WHERE produto_id = ? AND posicao_id = ?", (pid, posicao_id)).fetchone()

        if acao == "adicionar":
            if lote:
                conn.execute("UPDATE estoque_posicoes SET quantidade = quantidade + ? WHERE id = ?", (quantidade, lote["id"]))
            else:
                conn.execute("INSERT INTO estoque_posicoes (produto_id, posicao_id, quantidade) VALUES (?,?,?)", (pid, posicao_id, quantidade))
            conn.execute("INSERT INTO movimentacoes (produto_id, tipo, quantidade, observacao) VALUES (?,?,?,?)", (pid, "entrada", quantidade, "Ajuste via edição"))

        elif acao == "remover":
            if not lote or lote["quantidade"] < quantidade:
                raise ValueError("Quantidade insuficiente neste lote para remover.")
            
            conn.execute("UPDATE estoque_posicoes SET quantidade = quantidade - ? WHERE id = ?", (quantidade, lote["id"]))
            conn.execute("DELETE FROM estoque_posicoes WHERE quantidade <= 0 AND produto_id = ? AND posicao_id = ?", (pid, posicao_id))
            conn.execute("INSERT INTO movimentacoes (produto_id, tipo, quantidade, observacao) VALUES (?,?,?,?)", (pid, "saida", quantidade, "Ajuste via edição"))

        conn.commit()
        notify("mudanca")
        flash("Estoque atualizado com sucesso.", "ok")
    except Exception as e:
        conn.rollback()
        flash(f"Erro ao editar: {e}", "err")
    finally:
        conn.close()
    return redirect(url_for("produtos"))

@app.route("/produtos/<int:pid>/excluir", methods=["POST"])
def excluir_produto(pid):
    conn = get_conn()
    try:
        saldo = conn.execute("SELECT SUM(quantidade) FROM estoque_posicoes WHERE produto_id = ?", (pid,)).fetchone()[0]
        
        if saldo and saldo > 0:
            raise ValueError("Não é possível excluir um produto com estoque. Zere-o primeiro.")
            
        conn.execute("DELETE FROM produtos WHERE id = ?", (pid,))
        conn.commit()
        notify("mudanca")
        flash("Produto excluído permanentemente.", "ok")
    except Exception as e:
        conn.rollback()
        flash(f"Erro ao excluir: {e}", "err")
    finally:
        conn.close()
    return redirect(url_for("produtos"))

@app.route("/alertas")
def alertas():
    conn = get_conn()
    rows = conn.execute("""
        SELECT p.*, 
               COALESCE((SELECT SUM(quantidade) FROM estoque_posicoes ep WHERE ep.produto_id = p.id), 0) as total
        FROM produtos p 
        WHERE COALESCE((SELECT SUM(quantidade) FROM estoque_posicoes ep WHERE ep.produto_id = p.id), 0) <= p.estoque_minimo
        ORDER BY total ASC
    """).fetchall()
    conn.close()
    return render_template("alertas.html", produtos=rows)

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="127.0.0.1", port=5000, threaded=True)