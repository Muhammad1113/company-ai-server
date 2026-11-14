import { useCallback, useEffect, useState } from 'react';
import { AuthProvider, useAuth } from './context/Auth.jsx';
import { ToastProvider, useToast } from './context/Toast.jsx';
import { useChat } from './hooks/useChat.js';
import { api, describeError } from './lib/services.js';
import Login from './components/Login.jsx';
import Sidebar from './components/Sidebar.jsx';
import ChatView from './components/ChatView.jsx';
import Admin from './components/Admin.jsx';
import Calculator from './components/Calculator.jsx';
import PasswordDialog from './components/PasswordDialog.jsx';
import { Confirm } from './components/Modal.jsx';

import { ALL_COLLECTIONS } from './lib/config.js';

function Shell() {
  const { user, logout } = useAuth();
  const { push } = useToast();

  const [conversations, setConversations] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [collections, setCollections] = useState([]);
  const [view, setView] = useState('chat');
  const [drawer, setDrawer] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [toDelete, setToDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const refreshList = useCallback(async () => {
    try {
      setConversations(await api.listConversations());
    } catch (err) {
      if (err?.status !== 401) push(describeError(err, 'Could not load conversations'), 'error');
    } finally {
      setLoaded(true);
    }
  }, [push]);

  const chat = useChat({ onListChanged: refreshList, notify: push });

  useEffect(() => {
    refreshList();
    api.listCollections().then(setCollections).catch(() => {});
  }, [refreshList]);

  // Mode chunna khatam: hamesha Auto ('*'). Backend khud faisla karta hai: documents, phir tools,
  // phir model ka apna (pre-trained) jawab. Internet backend .env se band hai (WEB_SEARCH_ENABLED=false). (Ek collection tak mehdood karna chahein to purana Mode selector wapas lagana hoga.)
  const collection = ALL_COLLECTIONS;

  const openConversation = (id) => { setView('chat'); setDrawer(false); chat.select(id); };

  async function rename(id, title) {
    try {
      await api.renameConversation(id, title);
      setConversations((cs) => cs.map((c) => (c.id === id ? { ...c, title } : c)));
    } catch (err) {
      if (err?.status !== 401) push(describeError(err, 'Could not rename'), 'error');
    }
  }

  async function confirmDelete() {
    setDeleting(true);
    try {
      await api.deleteConversation(toDelete.id);
      if (chat.activeId === toDelete.id) chat.select(null);
      setConversations((cs) => cs.filter((c) => c.id !== toDelete.id));
      setToDelete(null);
    } catch (err) {
      if (err?.status !== 401) push(describeError(err, 'Could not delete'), 'error');
      setToDelete(null);
    } finally {
      setDeleting(false);
    }
  }

  const title = conversations.find((c) => c.id === chat.activeId)?.title || 'New chat';

  return (
    <div className="app">
     <div className={`shell ${collapsed ? 'collapsed' : ''}`}>
      <Sidebar
        conversations={conversations}
        activeId={chat.activeId}
        loaded={loaded}
        onSelect={openConversation}
        onNew={() => openConversation(null)}
        onRename={rename}
        onDelete={setToDelete}
        user={user}
        view={view}
        onView={(v) => { setView(v); setDrawer(false); }}
        onPassword={() => setPasswordOpen(true)}
        onLogout={logout}
        open={drawer}
        onClose={() => setDrawer(false)}
        onCollapse={() => setCollapsed(true)}
      />

      <main className="main">
        {view === 'admin' && (user.role === 'admin' || user.role === 'company_admin') ? (
          <Admin user={user} onClose={() => setView('chat')} onCollectionsChanged={setCollections} />
        ) : view === 'calc' ? (
          <Calculator onMenu={() => setDrawer(true)} />
        ) : (
          <ChatView
            chat={chat}
            collection={collection}
            onMenu={() => setDrawer(true)}
            onExpand={() => setCollapsed(false)}
            title={title}
            user={user}
            onNew={() => openConversation(null)}
            onDelete={() => chat.activeId && setToDelete({ id: chat.activeId, title })}
            onCalc={() => { setView('calc'); setCollapsed(false); }}
          />
        )}
      </main>
     </div>

      {passwordOpen && <PasswordDialog onClose={() => setPasswordOpen(false)} />}
      {toDelete && (
        <Confirm
          title="Delete conversation?"
          message={`“${toDelete.title}” and all its messages will be permanently deleted.`}
          onConfirm={confirmDelete}
          onClose={() => !deleting && setToDelete(null)}
          busy={deleting}
        />
      )}
    </div>
  );
}

function Gate() {
  const { user } = useAuth();
  return user ? <Shell key={user.username} /> : <Login />;
}

export default function App() {
  return (
    <ToastProvider>
      <AuthProvider>
        <Gate />
      </AuthProvider>
    </ToastProvider>
  );
}
