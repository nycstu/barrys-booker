#!/bin/bash
# Setup script for Barry's Auto-Booker

set -e
cd "$(dirname "$0")"

echo "=== Barry's Auto-Booker Setup ==="

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found. Install Python 3.9+"
    exit 1
fi

echo "Python: $(python3 --version)"

# Create venv
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate and install deps
source venv/bin/activate
echo "Installing dependencies..."
pip install -r requirements.txt
playwright install chromium

# Create .env if needed
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "*** IMPORTANT: Edit .env with your Barry's login credentials ***"
    echo "  File: $(pwd)/.env"
    echo ""
fi

echo ""
echo "Setup complete! Next steps:"
echo ""
echo "1. Edit .env with your Barry's email and password"
echo "2. Test with:  source venv/bin/activate && python3 book_barrys.py"
echo "   (First run: use HEADLESS=false to watch and debug)"
echo "   HEADLESS=false python3 book_barrys.py"
echo ""
echo "3. Install the weekly schedule:"
echo "   ./install_schedule.sh"
echo ""
