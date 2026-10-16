import { useEffect, useState } from "react";
import { fetchSiTypeDocuments, openFifDocument, type FifDocument } from "../api";

/** Методика поверки и описание типа — кнопками, файл открывается в новой вкладке. */
export function FifDocumentButtons({ documents }: { documents: FifDocument[] }) {
  const [error, setError] = useState("");
  if (documents.length === 0) return null;
  return (
    <div className="doc-row">
      {documents.map((doc) => (
        <button
          type="button"
          key={doc.id}
          className="doc-chip"
          title={doc.downloaded ? "Файл сохранён у нас" : "Откроется из ФГИС"}
          onClick={() => {
            setError("");
            openFifDocument(doc).catch((exc) => setError(exc instanceof Error ? exc.message : "Не удалось открыть"));
          }}
        >
          📄 {doc.title}
        </button>
      ))}
      {error && <div className="error">{error}</div>}
    </div>
  );
}

/** Документы по нашему типу СИ (если он заведён из ФИФ). Без связи — ничего не показываем. */
export default function SiTypeDocuments({ siTypeId }: { siTypeId: number }) {
  const [documents, setDocuments] = useState<FifDocument[]>([]);
  useEffect(() => {
    let cancelled = false;
    fetchSiTypeDocuments(siTypeId)
      .then((list) => !cancelled && setDocuments(list))
      .catch(() => !cancelled && setDocuments([]));
    return () => {
      cancelled = true;
    };
  }, [siTypeId]);
  return <FifDocumentButtons documents={documents} />;
}
