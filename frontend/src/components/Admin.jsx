import { useCallback, useEffect, useState } from 'react';
import { api } from '../lib/services.js';
import OverviewTab from './admin/OverviewTab.jsx';
import CompaniesTab from './admin/CompaniesTab.jsx';
import UsersTab from './admin/UsersTab.jsx';
import DocumentsTab from './admin/DocumentsTab.jsx';
import RegulatoryTab from './admin/RegulatoryTab.jsx';

// Super admin: Overview, Companies, Users, Documents.   Company admin: Users, Documents (sirf apni company).
export default function Admin({ user, onClose, onCollectionsChanged }) {
  const isSuper = user.role === 'admin';
  const [tab, setTab] = useState(isSuper ? 'overview' : 'users');
  const [companies, setCompanies] = useState([]);
  const [userCompany, setUserCompany] = useState('');      // Companies tab se "Users" dabane par filter
  const [focusCompany, setFocusCompany] = useState('');
  const [pending, setPending] = useState(0);              // openFDA updates jo approval maangte hain

  useEffect(() => {
    if (isSuper) api.adminListCompanies().then(setCompanies).catch(() => {});
  }, [isSuper]);

  useEffect(() => {
    if (!isSuper) return undefined;
    let alive = true;
    const check = () => api.regOpenfda().then((r) => alive && setPending(r.pending_count)).catch(() => {});
    check();
    const t = setInterval(check, 60000);
    return () => { alive = false; clearInterval(t); };
  }, [isSuper]);

  const onCompaniesChanged = useCallback((list) => setCompanies(list), []);
  const tabs = isSuper
    ? [['overview', 'Overview'], ['companies', 'Companies'], ['users', 'Users'], ['docs', 'Documents'], ['regulatory', 'Regulatory']]
    : [['users', 'Users'], ['docs', 'Documents']];

  return (
    <section className="admin">
      <header className="chat-head">
        <button type="button" className="btn" onClick={onClose}>Back to chat</button>
        <h1>{isSuper ? 'Admin' : 'My company'}</h1>
      </header>
      <div className="tabs" role="tablist">
        {tabs.map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? 'on' : ''}
            onClick={() => { setTab(id); if (id !== 'users') setUserCompany(''); }}>
            {label}{id === 'regulatory' && pending > 0 ? <span className="badge-count">{pending}</span> : null}
          </button>
        ))}
      </div>
      {isSuper && pending > 0 && tab !== 'regulatory' && (
        <div className="notice">
          {pending === 1 ? 'An FDA update is' : `${pending} FDA updates are`} waiting for your approval.
          <button type="button" className="link-btn" onClick={() => setTab('regulatory')}>Review</button>
        </div>
      )}
      <div className="scroll">
        <div className="admin-body wide">
          {tab === 'overview' && isSuper && (
            <OverviewTab
              onOpenCompany={(slug) => { setFocusCompany(slug); setTab('companies'); }}
              onOpenUsers={(slug) => { setUserCompany(slug); setTab('users'); }}
              onNavigate={(id) => { setUserCompany(''); setTab(id); }}
            />
          )}
          {tab === 'companies' && isSuper && (
            <CompaniesTab focus={focusCompany} onChanged={onCompaniesChanged}
              onOpenUsers={(slug) => { setUserCompany(slug); setTab('users'); }} />
          )}
          {tab === 'users' && (
            <UsersTab mode={isSuper ? 'super' : 'company'} me={user.username} companies={companies} initialCompany={userCompany} />
          )}
          {tab === 'regulatory' && isSuper && <RegulatoryTab onPendingChanged={setPending} />}
          {tab === 'docs' && (
            <DocumentsTab mode={isSuper ? 'super' : 'company'} companySlug={user.company} companies={companies}
              onCollectionsChanged={onCollectionsChanged} />
          )}
        </div>
      </div>
    </section>
  );
}
