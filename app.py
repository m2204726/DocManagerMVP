import os
import sqlite3
import pandas as pd
import streamlit as st
from datetime import datetime
import uuid

# --- НАСТРОЙКА БАЗЫ ДАННЫХ ---
DB_URL = os.getenv("DATABASE_URL")
if DB_URL and DB_URL.startswith("postgres"):
    import psycopg2
    def get_db():
        return psycopg2.connect(DB_URL)
else:
    def get_db():
        conn = sqlite3.connect("local_db.sqlite", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, role TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS projects 
                      (id INTEGER PRIMARY KEY, name TEXT UNIQUE)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS documents 
                      (id INTEGER PRIMARY KEY, project_id INTEGER, name TEXT, tags TEXT, 
                       created_by TEXT, created_at TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS versions 
                      (id INTEGER PRIMARY KEY, document_id INTEGER, version_num INTEGER, 
                       file_path TEXT, uploaded_by TEXT, uploaded_at TEXT)''')
    
    cursor.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES ('admin', '1234', 'admin')")
    cursor.execute("INSERT OR IGNORE INTO projects (name) VALUES ('Общий проект')")
    conn.commit()
    conn.close()

os.makedirs("uploads", exist_ok=True)
init_db()

# --- УПРАВЛЕНИЕ СЕССИЕЙ ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""

def login():
    st.title("Вход в систему управления документами")
    user = st.text_input("Логин (попробуйте: admin)")
    pwd = st.text_input("Пароль (попробуйте: 1234)", type="password")
    if st.button("Войти"):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username=? AND password=?", (user, pwd))
        result = cursor.fetchone()
        conn.close()
        if result:
            st.session_state.logged_in = True
            st.session_state.username = user
            st.rerun()
        else:
            st.error("Неверный логин или пароль")

# --- ОСНОВНОЙ ИНТЕРФЕЙС ---
def main():
    st.set_page_config(page_title="DocManager MVP", layout="wide")
    st.sidebar.title(f"Привет, {st.session_state.username}!")
    if st.sidebar.button("Выйти"):
        st.session_state.logged_in = False
        st.rerun()

    st.title("📁 Хранилище документов")

    # 1. ЗАГРУЗКА
    st.header("Загрузить новый документ или версию")
    with st.form("upload_form"):
        col1, col2 = st.columns(2)
        with col1:
            project = st.selectbox("Проект", pd.read_sql_query("SELECT name FROM projects", get_db())['name'].tolist())
            doc_name = st.text_input("Название документа")
            tags = st.text_input("Теги (через запятую)", "отчет, 2024")
        with col2:
            uploaded_file = st.file_uploader("Выберите файл", type=['pdf', 'docx', 'xlsx', 'txt', 'png'])
            description = st.text_area("Описание изменений")
        
        submitted = st.form_submit_button("Загрузить")
        if submitted and uploaded_file is not None and doc_name:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM documents WHERE name=? AND project_id=(SELECT id FROM projects WHERE name=?)", (doc_name, project))
            doc = cursor.fetchone()
            
            file_ext = uploaded_file.name.split('.')[-1]
            unique_filename = f"{uuid.uuid4().hex}.{file_ext}"
            file_path = os.path.join("uploads", unique_filename)
            with open(file_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            
            if doc:
                doc_id = doc['id'] if isinstance(doc, dict) else doc[0]
                cursor.execute("SELECT MAX(version_num) FROM versions WHERE document_id=?", (doc_id,))
                res = cursor.fetchone()
                max_ver = (res[0] if isinstance(res, tuple) else res['MAX(version_num)']) or 0
                new_ver = max_ver + 1
                cursor.execute("INSERT INTO versions (document_id, version_num, file_path, uploaded_by, uploaded_at) VALUES (?, ?, ?, ?, ?)",
                               (doc_id, new_ver, file_path, st.session_state.username, datetime.now().isoformat()))
                st.success(f"Добавлена версия {new_ver} для документа '{doc_name}'")
            else:
                cursor.execute("INSERT INTO documents (project_id, name, tags, created_by, created_at) VALUES ((SELECT id FROM projects WHERE name=?), ?, ?, ?, ?)",
                               (project, doc_name, tags, st.session_state.username, datetime.now().isoformat()))
                doc_id = cursor.lastrowid
                cursor.execute("INSERT INTO versions (document_id, version_num, file_path, uploaded_by, uploaded_at) VALUES (?, ?, ?, ?, ?)",
                               (doc_id, 1, file_path, st.session_state.username, datetime.now().isoformat()))
                st.success(f"Создан новый документ '{doc_name}' (Версия 1)")
            conn.commit()
            conn.close()
            st.rerun()

    st.divider()

    # 2. ПОИСК И КАТАЛОГ
    st.header("Поиск и каталог документов")
    col1, col2, col3 = st.columns(3)
    with col1:
        search_query = st.text_input("Поиск по названию")
    with col2:
        filter_project = st.selectbox("Фильтр по проекту", ["Все"] + pd.read_sql_query("SELECT name FROM projects", get_db())['name'].tolist())
    with col3:
        filter_tag = st.text_input("Фильтр по тегу")

    query = """
        SELECT d.name as 'Название', p.name as 'Проект', d.tags as 'Теги', 
               MAX(v.version_num) as 'Версия', d.created_by as 'Автор', 
               MAX(v.uploaded_at) as 'Последнее обновление'
        FROM documents d
        JOIN projects p ON d.project_id = p.id
        JOIN versions v ON d.id = v.document_id
    """
    conditions = []
    if search_query:
        conditions.append(f"d.name LIKE '%{search_query}%'")
    if filter_project != "Все":
        conditions.append(f"p.name = '{filter_project}'")
    if filter_tag:
        conditions.append(f"d.tags LIKE '%{filter_tag}%'")
    
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " GROUP BY d.id ORDER BY MAX(v.uploaded_at) DESC"

    conn = get_db()
    try:
        df = pd.read_sql_query(query, conn)
        st.dataframe(df, use_container_width=True)

        # 3. ДЕЙСТВИЯ С ДОКУМЕНТОМ
        if not df.empty:
            st.subheader("Действия с документом")
            selected_doc = st.selectbox("Выберите документ", df['Название'].tolist())
            
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM documents WHERE name=?", (selected_doc,))
            doc_id = cursor.fetchone()[0]
            
            # Получаем версии (добавили id версии для возможного удаления в будущем)
            cursor.execute("SELECT id, version_num, file_path, uploaded_by, uploaded_at FROM versions WHERE document_id=? ORDER BY version_num DESC", (doc_id,))
            versions = cursor.fetchall()
            conn.close()

            st.write(f"**История версий для '{selected_doc}':**")
            for ver in versions:
                ver_id = ver[0] if isinstance(ver, tuple) else ver['id']
                ver_num = ver[1] if isinstance(ver, tuple) else ver['version_num']
                f_path = ver[2] if isinstance(ver, tuple) else ver['file_path']
                author = ver[3] if isinstance(ver, tuple) else ver['uploaded_by']
                date = ver[4] if isinstance(ver, tuple) else ver['uploaded_at']
                
                with open(f_path, "rb") as file:
                    file_bytes = file.read()
                
                col_dl, col_sp = st.columns([4, 1])
                with col_dl:
                    st.download_button(
                        label=f"⬇️ Скачать Версию {ver_num} (от {date[:10]}, автор: {author})",
                        data=file_bytes,
                        file_name=f"{selected_doc}_v{ver_num}.{f_path.split('.')[-1]}",
                        mime="application/octet-stream",
                        key=f"dl_{ver_id}"
                    )

            # 4. ФУНКЦИЯ УДАЛЕНИЯ (НОВОЕ)
            st.divider()
            st.subheader("⚠️ Управление документам")
            st.warning("Удаление документа удалит его из списка и безвозвратно удалит все связанные файлы с сервера.")
            
            doc_to_delete = st.selectbox("Выберите документ для полного удаления", df['Название'].tolist(), key="delete_select")
            
            if st.button(f"🗑️ Удалить документ '{doc_to_delete}' и все его версии", type="primary"):
                conn = get_db()
                cursor = conn.cursor()
                
                # 1. Находим все файлы и удаляем их физически
                cursor.execute("SELECT file_path FROM versions WHERE document_id=(SELECT id FROM documents WHERE name=?)", (doc_to_delete,))
                files = cursor.fetchall()
                for f in files:
                    f_path = f[0] if isinstance(f, tuple) else f['file_path']
                    if os.path.exists(f_path):
                        os.remove(f_path)
                
                # 2. Удаляем записи из базы данных
                cursor.execute("DELETE FROM versions WHERE document_id=(SELECT id FROM documents WHERE name=?)", (doc_to_delete,))
                cursor.execute("DELETE FROM documents WHERE name=?", (doc_to_delete,))
                conn.commit()
                conn.close()
                
                st.success(f"✅ Документ '{doc_to_delete}' и все его файлы успешно удалены!")
                st.rerun() # Перезагружаем страницу, чтобы список обновился
                
    except Exception as e:
        st.error(f"Ошибка чтения данных: {e}")

if __name__ == "__main__":
    if not st.session_state.logged_in:
        login()
    else:
        main()
