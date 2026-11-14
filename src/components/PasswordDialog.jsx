import { useState } from 'react';
import Modal from './Modal.jsx';
import { useAuth } from '../context/Auth.jsx';
import { useToast } from '../context/Toast.jsx';
import { describeError } from '../lib/services.js';

export default function PasswordDialog({ onClose }) {
  const { changePassword } = useAuth();
  const { push } = useToast();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [again, setAgain] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    if (next.length < 8 || next.length > 128) return setError('New password must be 8 to 128 characters.');
    if (next !== again) return setError('The new passwords do not match.');
    if (next === current) return setError('Choose a password different from the current one.');
    setBusy(true);
    setError('');
    try {
      await changePassword(current, next);
      push('Password changed. Other devices were signed out.', 'success');
      onClose();
    } catch (err) {
      setError(describeError(err, 'Could not change the password'));
      setBusy(false);
    }
  }

  return (
    <Modal title="Change password" onClose={onClose}>
      <form onSubmit={submit} className="form">
        <label>Current password
          <input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
        </label>
        <label>New password
          <input type="password" autoComplete="new-password" minLength={8} maxLength={128} value={next} onChange={(e) => setNext(e.target.value)} required />
        </label>
        <label>Repeat new password
          <input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required />
        </label>
        <p className="hint">Changing it signs you out of every other device.</p>
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>{busy ? 'Saving…' : 'Change password'}</button>
        </div>
      </form>
    </Modal>
  );
}
