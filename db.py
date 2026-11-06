# language: Python, file: db.py
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "estoque.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")
    
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS posicoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                corredor TEXT NOT NULL,
                prateleira TEXT NOT NULL,
                descricao TEXT
            );

            CREATE TABLE IF NOT EXISTS produtos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT UNIQUE NOT NULL,
                nome TEXT NOT NULL,
                categoria TEXT NOT NULL,
                preco REAL NOT NULL DEFAULT 0,
                estoque_minimo INTEGER NOT NULL DEFAULT 5,
                criado_em TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS estoque_posicoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                produto_id INTEGER NOT NULL,
                posicao_id INTEGER NOT NULL,
                quantidade INTEGER NOT NULL DEFAULT 0,
                UNIQUE(produto_id, posicao_id),
                FOREIGN KEY(produto_id) REFERENCES produtos(id) ON DELETE CASCADE,
                FOREIGN KEY(posicao_id) REFERENCES posicoes(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS movimentacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                produto_id INTEGER NOT NULL,
                tipo TEXT NOT NULL CHECK(tipo IN ('entrada','saida')),
                quantidade INTEGER NOT NULL,
                observacao TEXT,
                data TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(produto_id) REFERENCES produtos(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS transferencias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                produto_id INTEGER NOT NULL,
                posicao_origem_id INTEGER,
                posicao_destino_id INTEGER,
                quantidade INTEGER NOT NULL,
                data TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(produto_id) REFERENCES produtos(id) ON DELETE CASCADE
            );
        """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Erro ao criar o banco: {e}")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()