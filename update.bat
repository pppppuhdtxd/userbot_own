@echo off
echo Pulling latest changes...
git pull

echo Staging valid changes...
git add .

echo Committing...
git commit -m "auto-update via bat"

echo Pushing to remote...
git push

echo Update complete!
pause
