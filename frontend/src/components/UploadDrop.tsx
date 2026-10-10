import { useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { useUploadDocuments } from '@/api/generated'
import { errorText } from '@/lib/format'
import { cn } from '@/lib/utils'

const ACCEPT = {
  'application/pdf': ['.pdf'],
  'image/jpeg': ['.jpg', '.jpeg'],
  'image/png': ['.png'],
  'image/tiff': ['.tif', '.tiff'],
  'image/webp': ['.webp'],
  'image/heic': ['.heic', '.heif'],
}

// Drop invoices here: they go to the manual workspace, and the worker reads and checks them.
export function UploadDrop({ onUploaded }: { onUploaded: (workspaceId: number) => void }) {
  const upload = useUploadDocuments()
  const [message, setMessage] = useState<string | null>(null)
  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: ACCEPT,
    onDrop: (files) => {
      if (!files.length) return
      upload.mutate(
        { data: { files } },
        {
          onSuccess: (results) => {
            const ok = results.filter((r) => r.document_id != null)
            const bad = results.filter((r) => r.error)
            setMessage(`${ok.length} uploaded${bad.length ? `, ${bad.length} refused (${bad.map((b) => b.filename).join(', ')})` : ''}`)
            if (ok[0]?.workspace_id != null) onUploaded(ok[0].workspace_id)
          },
          onError: (e) => setMessage(`Upload failed: ${errorText(e)}`),
        },
      )
    },
  })
  return (
    <div
      {...getRootProps()}
      className={cn(
        'cursor-pointer rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground transition-colors',
        isDragActive && 'border-primary bg-accent text-foreground',
      )}
    >
      <input {...getInputProps()} />
      {upload.isPending ? 'Uploading…' : isDragActive ? 'Drop to upload' : 'Drop invoices here (PDF, photo, scan) or click to choose'}
      {message && !upload.isPending && <span className="ml-2 text-foreground">· {message}</span>}
    </div>
  )
}
