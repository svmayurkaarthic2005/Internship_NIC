#!/usr/bin/env python3
"""
Quick Setup Script for SIS Chatbot Portal
Automates the setup process where possible
"""
import sys
import os
import subprocess
import asyncio
from pathlib import Path

def print_step(step_num, message):
    """Print a formatted step message"""
    print(f"\n{'='*70}")
    print(f"STEP {step_num}: {message}")
    print(f"{'='*70}\n")

def print_success(message):
    """Print success message"""
    print(f"✅ {message}")

def print_error(message):
    """Print error message"""
    print(f"❌ {message}")

def print_warning(message):
    """Print warning message"""
    print(f"⚠️  {message}")

def check_env_file():
    """Check if .env file exists"""
    print_step(1, "Checking Environment File")
    
    env_path = Path(".env")
    if env_path.exists():
        print_success(".env file exists")
        return True
    else:
        print_error(".env file not found")
        print("Please create .env file. See .env.example for reference")
        return False

def check_ollama():
    """Check if Ollama is running and models are available"""
    print_step(2, "Checking Ollama")
    
    try:
        import requests
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        if response.status_code == 200:
            print_success("Ollama is running")
            
            models = response.json().get('models', [])
            model_names = [m['name'] for m in models]
            
            # Check for required models
            has_llm = any('llama3.1' in m for m in model_names)
            has_embed = any('nomic-embed-text' in m for m in model_names)
            
            if has_llm:
                print_success("LLM model (llama3.1) found")
            else:
                print_warning("LLM model not found. Run: ollama pull llama3.1:8b")
            
            if has_embed:
                print_success("Embedding model (nomic-embed-text) found")
            else:
                print_warning("Embedding model not found. Run: ollama pull nomic-embed-text")
            
            return has_llm and has_embed
        else:
            print_error(f"Ollama returned status {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Ollama not accessible: {e}")
        print("Please start Ollama: ollama serve")
        return False

async def check_database():
    """Check database connection"""
    print_step(3, "Checking Database Connection")
    
    try:
        from backend.database import engine
        from sqlalchemy import text
        
        async with engine.connect() as conn:
            await conn.execute(text('SELECT 1'))
            print_success("Database connection successful")
            return True
    except Exception as e:
        print_error(f"Database connection failed: {e}")
        print("\nPlease check:")
        print("1. PostgreSQL is running")
        print("2. Database 'sis_chatbot' exists (create with: createdb sis_chatbot)")
        print("3. Credentials in .env file are correct")
        return False

async def check_database_seeded():
    """Check if database has been seeded"""
    print_step(4, "Checking Database Data")
    
    try:
        from backend.database import get_db
        from backend.models import SISOfficer
        from sqlalchemy import select
        
        async for db in get_db():
            result = await db.execute(select(SISOfficer))
            officers = result.scalars().all()
            
            if len(officers) >= 3:
                print_success(f"Database has {len(officers)} officers")
                return True
            else:
                print_warning(f"Database has {len(officers)} officers (expected 3)")
                print("Run: python backend/seed.py")
                return False
    except Exception as e:
        print_warning("Cannot check database data (tables may not exist)")
        print("Run: python backend/seed.py")
        return False

def check_pgvector():
    """Check pgvector store status"""
    print_step(5, "Checking pgvector store")
    
    try:
        from backend.services.pgvector_store import get_collection_stats
        
        stats = get_collection_stats()
        doc_count = stats.get('document_count', 0)
        
        if doc_count > 0:
            print_success(f"pgvector store has {doc_count} documents")
            return True
        else:
            print_warning("pgvector store is empty")
            print("Run: python backend/ingest.py")
            return False
    except Exception as e:
        print_error(f"pgvector error: {e}")
        return False

def print_summary(checks):
    """Print setup summary"""
    print("\n" + "="*70)
    print("SETUP SUMMARY")
    print("="*70 + "\n")
    
    all_good = all(checks.values())
    
    for check_name, status in checks.items():
        icon = "✅" if status else "❌"
        print(f"{icon} {check_name}")
    
    print("\n" + "="*70)
    
    if all_good:
        print("🎉 ALL CHECKS PASSED - System is ready!")
        print("\nNext steps:")
        print("1. Start backend: uvicorn backend.main:app --reload --port 8000")
        print("2. Start frontend: Open frontend/login.html with Live Server")
        print("3. Login with: arjun.kumar@sis.tn.gov.in / Test@1234")
        print("4. Run tests: python test_integration.py")
    else:
        print("⚠️  SETUP INCOMPLETE - Please fix issues above")
        print("\nFollow these guides:")
        print("- SETUP_INSTRUCTIONS.md - Step-by-step setup")
        print("- README.md - Full documentation")
    
    print("="*70)

async def main():
    """Main setup function"""
    print("="*70)
    print("SIS CHATBOT PORTAL - QUICK SETUP")
    print("="*70)
    
    checks = {}
    
    # Run checks
    checks["Environment File"] = check_env_file()
    checks["Ollama"] = check_ollama()
    
    if checks["Environment File"]:
        checks["Database Connection"] = await check_database()
        
        if checks["Database Connection"]:
            checks["Database Seeded"] = await check_database_seeded()
        else:
            checks["Database Seeded"] = False
        
        checks["pgvector"] = check_pgvector()
    else:
        checks["Database Connection"] = False
        checks["Database Seeded"] = False
        checks["pgvector"] = False
    
    # Print summary
    print_summary(checks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nSetup interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        sys.exit(1)
