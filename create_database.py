"""Create the sis_chatbot database if it doesn't exist"""
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

def create_database():
    """Create sis_chatbot database"""
    try:
        # Connect to default postgres database
        conn = psycopg2.connect(
            host="localhost",
            port=5432,
            user="postgres",
            password="Mayur@2005",
            database="postgres"
        )
        
        # Set autocommit mode for CREATE DATABASE
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        
        cursor = conn.cursor()
        
        # Check if database exists
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = 'sis_chatbot'")
        exists = cursor.fetchone()
        
        if exists:
            print("✅ Database 'sis_chatbot' already exists")
        else:
            # Create database
            cursor.execute("CREATE DATABASE sis_chatbot")
            print("✅ Database 'sis_chatbot' created successfully")
        
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Error creating database: {e}")
        print("\nPossible issues:")
        print("1. PostgreSQL service is not running")
        print("2. Password is incorrect")
        print("3. PostgreSQL is not installed")
        return False

if __name__ == "__main__":
    print("Creating database 'sis_chatbot'...")
    create_database()
