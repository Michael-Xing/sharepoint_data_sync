#!/bin/bash

# SharePoint Sync Deployment Script

set -e

echo "🚀 SharePoint Sync Deployment Script"
echo "==================================="

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "❌ uv is not installed. Please install uv first:"
    echo "   pip install uv"
    exit 1
fi

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "⚠️  .env file not found. Creating from template..."
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "✅ Created .env file from template. Please edit it with your configuration."
        echo "   nano .env"
        exit 1
    else
        echo "❌ .env.example not found. Please create .env file manually."
        exit 1
    fi
fi

echo "📦 Installing dependencies..."
uv sync

echo "🗄️  Creating database tables..."
uv run python main.py test

echo "✅ Deployment completed successfully!"
echo ""
echo "Available commands:"
echo "  • Start scheduler: uv run python main.py"
echo "  • Manual sync:    uv run python main.py sync"
echo "  • Manual cleanup: uv run python main.py cleanup"
echo ""
echo "For Docker deployment:"
echo "  • Build image:    docker build -t sharepoint-sync:latest ."
echo "  • Run container:  docker run --env-file .env -v \$(pwd)/data:/app/data sharepoint-sync:latest"
echo ""
echo "For Kubernetes deployment:"
echo "  • Apply configs:  kubectl apply -f k8s/"
echo "  • Check status:   kubectl get pods -l app=sharepoint-sync"


