from src.database.db import get_connection

def is_admin(user_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM admins WHERE telegram_id = ?', (user_id,))
        return cursor.fetchone() is not None