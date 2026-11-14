# Frontend (poora src folder): Mode selector khatam + har document ke saamne Delete + internet ka zikr khatam

Is zip mein POORA src/ folder hai. Purana src hata kar ye lagayen (file-file copy karne ki zaroorat nahi):

  cd D:\company-ai-server\frontend
  Rename-Item src src_old
  Expand-Archive D:\path\to\frontend_src_full.zip -DestinationPath . -Force
  npm run build

Check: Select-String -Path src\components\Composer.jsx -Pattern "Company documents first"   (line aani chahiye)
Browser: Ctrl+F5. Backend: backend_phase7_v6.zip + .env mein WEB_SEARCH_ENABLED=false
