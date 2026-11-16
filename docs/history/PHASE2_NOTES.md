# Phase 2 frontend (super admin portal)

Naya / badla:
- src/components/Admin.jsx                shell (role ke hisaab se tabs)
- src/components/admin/OverviewTab.jsx    online, load, companies ka table (15 sec mein refresh)
- src/components/admin/CompaniesTab.jsx   company banao / edit / enable-disable
- src/components/admin/UsersTab.jsx       super admin (company chun kar) aur company admin (sirf apni company)
- src/components/admin/DocumentsTab.jsx   Shared regulatory / company ke hisaab se upload; collection ka naam khud banta hai
- src/components/admin/common.js
- src/lib/api.js, Sidebar.jsx, App.jsx    company_admin ko "My company" menu, naye API calls
- src/styles.css                          neeche naye classes

Chalane ke liye (frontend folder mein): npm install  ->  npm run dev
Backend phase2 zip se server restart karna zaroori hai.
