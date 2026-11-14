import { useEffect, useState } from 'react';
import Modal from '../Modal.jsx';
import { describeError } from '../../lib/services.js';

/** Document ka text dekhne ke liye. load() => {title, parts:[{index,text}]} ya {title, text}. */
export default function DocViewer({ title, load, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    load().then((d) => alive && setData(d)).catch((e) => alive && setError(describeError(e, 'Could not load the document')));
    return () => { alive = false; };
  }, [load]);
  const parts = data ? (data.parts || [{ index: 1, text: data.text }]) : [];
  return (
    <Modal title={title} onClose={onClose} wide>
      {!data && !error && <p className="muted">Loading…</p>}
      {error && <p className="form-error">{error}</p>}
      {data && (
        <div className="doc-view">
          {parts.map((p) => (
            <section key={p.index}>
              {parts.length > 1 && <div className="muted small">Part {p.index} of {parts.length}</div>}
              <pre dir="auto">{p.text}</pre>
            </section>
          ))}
        </div>
      )}
      <div className="modal-actions"><button type="button" className="btn" onClick={onClose}>Close</button></div>
    </Modal>
  );
}
