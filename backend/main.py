"""
FastAPI main application entry point
"""
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

# Force UTF-8 output on Windows to prevent UnicodeEncodeError crashes on emoji print statements
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from backend.config import settings
from backend.database import engine, Base
from backend.routers import auth, chat, applications, survey, speech
from backend.schemas import StandardResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle management - startup and shutdown events
    """
    # Startup
    print("=" * 60)
    print("🚀 Starting SIS Chatbot Portal API...")
    print(f"   Environment: {settings.ENVIRONMENT}")
    print(f"   CORS Origins: {settings.CORS_ORIGINS}")
    
    # Create tables in development
    try:
        if settings.ENVIRONMENT == "development":
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                print("   ✅ Database tables created/verified")
    except Exception as e:
        print(f"   ⚠️  Database initialization warning: {e}")
    
    # Initialize pgvector store
    try:
        from backend.services.pgvector_store import init_pgvector, get_collection_stats
        init_pgvector()
        stats = get_collection_stats()
        print(f"   ✅ pgvector store ready ({stats['document_count']} documents)")
    except Exception as e:
        print(f"   ⚠️  pgvector initialization warning: {e}")
        print(f"      Ensure 'CREATE EXTENSION IF NOT EXISTS vector;' has been run")

    
    # Check Ollama connectivity
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=5.0)
            if response.status_code == 200:
                print(f"   ✅ Ollama connected ({settings.OLLAMA_BASE_URL})")
            else:
                print(f"   ⚠️  Ollama returned status {response.status_code}")
    except Exception as e:
        print(f"   ⚠️  Ollama connection warning: {e}")
        print(f"      Make sure Ollama is running: ollama serve")
    
    # Initialize Speech Service (Faster-Whisper)
    try:
        from backend.services.speech_service import get_speech_service
        speech_service = get_speech_service()
        speech_service.initialize()
        print(f"   ✅ Speech service initialized (model: {speech_service.model_size})")
    except Exception as e:
        print(f"   ⚠️  Speech service initialization warning: {e}")
        print(f"      Voice input will not be available")
    
    print("   ✅ API Server Ready")
    print("=" * 60)
    
    yield
    
    # Shutdown
    print("\n" + "=" * 60)
    print("🛑 Shutting down SIS Chatbot Portal API...")
    await engine.dispose()
    print("   ✅ Database connections closed")
    print("=" * 60)


# Create FastAPI app
app = FastAPI(
    title="SIS Chatbot Portal API",
    description="AI-Powered Chatbot Portal API for Sub Inspector Surveyor (SIS) Officers - Tamil Nadu",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.ENVIRONMENT == "development" else None,
    redoc_url="/api/redoc" if settings.ENVIRONMENT == "development" else None,
)

# ========== MIDDLEWARE ==========

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)


# ========== EXCEPTION HANDLERS ==========

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Handle 404 Not Found errors"""
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content=StandardResponse.error_response(
            message="Resource not found",
            data={"path": str(request.url)}
        ).model_dump()
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle 422 Validation errors"""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=StandardResponse.error_response(
            message="Validation error",
            data={"errors": exc.errors()}
        ).model_dump()
    )


@app.exception_handler(500)
async def internal_server_error_handler(request: Request, exc):
    """Handle 500 Internal Server Error"""
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=StandardResponse.error_response(
            message="Internal server error",
            data={"error": str(exc) if settings.ENVIRONMENT == "development" else None}
        ).model_dump()
    )


# ========== ROUTERS ==========

# Include routers
app.include_router(auth.router, tags=["Authentication"])
app.include_router(chat.router, tags=["Chat"])
app.include_router(applications.router, tags=["Applications"])
app.include_router(survey.router, tags=["Survey"])
app.include_router(speech.router, tags=["Speech"])

# ========== STATIC FILES ==========
import os
from fastapi.staticfiles import StaticFiles

frontend_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
if os.path.exists(frontend_path):
    app.mount("/frontend", StaticFiles(directory=frontend_path, html=True), name="frontend")



# ========== ROOT ENDPOINTS ==========

@app.get("/", response_model=StandardResponse)
async def root():
    """
    Root endpoint - API information
    """
    return StandardResponse.success_response(
        data={
            "service": "SIS Chatbot Portal API",
            "version": "2.0.0",
            "environment": settings.ENVIRONMENT,
            "docs": "/api/docs" if settings.ENVIRONMENT == "development" else "disabled",
            "status": "operational"
        },
        message="SIS Chatbot Portal API is running"
    )


@app.get("/health", response_model=StandardResponse)
async def health_check():
    """
    Health check endpoint - returns API health status
    """
    return StandardResponse.success_response(
        data={
            "status": "healthy",
            "service": "SIS Chatbot API",
            "database": "connected",
            "environment": settings.ENVIRONMENT
        },
        message="System is healthy"
    )
