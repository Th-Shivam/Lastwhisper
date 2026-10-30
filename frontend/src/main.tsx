import { useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Archive,
  AudioLines,
  Bookmark,
  BookOpen,
  Check,
  ChevronLeft,
  Clock,
  Edit3,
  ExternalLink,
  FileAudio,
  Grid2X2,
  LayoutList,
  LoaderCircle,
  Menu,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Sparkles,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import {
  api,
  type IndexStatus,
  type Recording,
  type SavedMemory,
  type SearchResult,
  type Stats,
} from './api'
import './styles.css'

type View = 'dashboard' | 'recordings' | 'detail' | 'search' | 'settings'

const parseDateValue = (value: string) => new Date(/(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`)
const formatDate = (value: string) => new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
}).format(parseDateValue(value))

const duration = (value: number | null) => {
  if (value == null) return '—'
  const seconds = Math.max(0, Math.floor(value))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

type ExtractedItem = {value:string;segment_ids:string[]}
type ExtractedMemories = {key_topics?:string[];people?:ExtractedItem[];places?:ExtractedItem[];dates?:ExtractedItem[];action_items?:ExtractedItem[]}
const parseExtractedMemories = (value?: string | null): ExtractedMemories | null => {
  if (!value) return null
  try {
    const parsed = JSON.parse(value) as Record<string, unknown>
    const result: ExtractedMemories = {}
    if (Array.isArray(parsed.key_topics)) result.key_topics = parsed.key_topics.filter((item): item is string => typeof item === 'string')
    for (const key of ['people', 'places', 'dates', 'action_items'] as const) {
      if (!Array.isArray(parsed[key])) continue
      result[key] = parsed[key].filter((item): item is ExtractedItem => {
        if (!item || typeof item !== 'object') return false
        const candidate = item as {value?:unknown;segment_ids?:unknown}
        return typeof candidate.value === 'string' && Array.isArray(candidate.segment_ids) && candidate.segment_ids.every((id: unknown) => typeof id === 'string')
      })
    }
    return result
  } catch { return null }
}

function Wave() {
  return <div className="wave" aria-hidden="true">
    {[13, 25, 18, 34, 22, 38, 16, 28, 12, 30, 19, 34, 17, 24, 11, 29].map((height, index) => (
      <i key={index} style={{ height }} />
    ))}
  </div>
}

function Status({ status }: { status: string }) {
  const label = status === 'complete'
    ? 'Transcribed'
    : status === 'failed'
      ? 'Needs attention'
      : status === 'processing' || status === 'pending'
        ? 'Transcribing'
        : status === 'cancelling'
          ? 'Cancelling'
          : status === 'cancelled'
            ? 'Cancelled'
            : 'Awaiting transcription'
  return <span className={`status ${status}`}>{label}</span>
}

function App() {
  const [view, setView] = useState<View>('dashboard')
  const [records, setRecords] = useState<Recording[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [backgroundError, setBackgroundError] = useState('')
  const [selected, setSelected] = useState<Recording | null>(null)
  const [seekSeconds, setSeekSeconds] = useState<number | null>(null)
  const [upload, setUpload] = useState<{ progress: number; error: string } | null>(null)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [dateFilter, setDateFilter] = useState('all')
  const [grid, setGrid] = useState(true)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [detailReturnView, setDetailReturnView] = useState<View>('recordings')
  const fileRef = useRef<HTMLInputElement>(null)
  const mobileMenuRef = useRef<HTMLButtonElement>(null)
  const mobileNavRef = useRef<HTMLElement>(null)
  const uploadActiveRef = useRef(false)
  const uploadGenerationRef = useRef(0)
  const uploadDismissTimerRef = useRef(0)

  const load = async (silent = false) => {
    if (!silent) { setLoading(true); setError('') }
    try {
      const [recordingsResult, statsResult] = await Promise.allSettled([api.recordings(), api.stats()])
      if (recordingsResult.status === 'rejected') throw recordingsResult.reason
      setRecords(recordingsResult.value)
      if (statsResult.status === 'fulfilled') setStats(statsResult.value)
      if (silent) setBackgroundError('')
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : 'Could not load your archive.'
      if (silent) setBackgroundError(`Live transcription updates paused: ${message}`)
      else setError(message)
    } finally {
      if (!silent) setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const activeRecordings = records.some((recording) => ['pending', 'processing', 'cancelling'].includes(recording.transcription_status))
  useEffect(() => {
    if (!activeRecordings) return
    let stopped = false
    let timer = 0
    const poll = async () => {
      await load(true)
      if (!stopped) timer = window.setTimeout(() => void poll(), 2000)
    }
    timer = window.setTimeout(() => void poll(), 2000)
    return () => { stopped = true; window.clearTimeout(timer) }
  }, [activeRecordings])

  useEffect(() => {
    if (!mobileNavOpen) return
    const panel = mobileNavRef.current
    const focusable = Array.from(panel?.querySelectorAll<HTMLElement>('button,[href],[tabindex]:not([tabindex="-1"])') ?? [])
    focusable[0]?.focus()
    const manageDrawerKeyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setMobileNavOpen(false); mobileMenuRef.current?.focus(); return }
      if (event.key !== 'Tab' || focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', manageDrawerKeyboard)
    return () => window.removeEventListener('keydown', manageDrawerKeyboard)
  }, [mobileNavOpen])

  const open = (recording: Recording, atSeconds: number | null = null, returnView: View = 'recordings') => {
    setSelected(recording)
    setSeekSeconds(atSeconds)
    setDetailReturnView(returnView)
    setView('detail')
  }

  const openSource = async (recordingId: string, atSeconds: number) => {
    setError('')
    try {
      const recording = records.find((item) => item.id === recordingId) ?? await api.get(recordingId)
      open(recording, atSeconds, 'search')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not open the source recording.')
    }
  }

  const refreshStats = async () => {
    try {
      setStats(await api.stats())
    } catch {
      // The search view still works if the dashboard metric cannot refresh.
    }
  }

  const uploadFile = async (file: File) => {
    if (uploadActiveRef.current) return
    const accepted = ['audio/wav', 'audio/x-wav', 'audio/mpeg', 'audio/mp4', 'audio/x-m4a', 'audio/flac', 'audio/x-flac']
    if (!accepted.includes(file.type) && !/[.](wav|mp3|m4a|flac)$/i.test(file.name)) {
      setUpload({ progress: 0, error: 'Choose a WAV, MP3, M4A, or FLAC file.' })
      return
    }
    uploadActiveRef.current = true
    const generation = ++uploadGenerationRef.current
    window.clearTimeout(uploadDismissTimerRef.current)
    setUpload({ progress: 0, error: '' })
    try {
      await api.upload(file, (progress) => { if (generation === uploadGenerationRef.current) setUpload({ progress, error: '' }) })
      if (generation === uploadGenerationRef.current) setUpload({ progress: 100, error: '' })
      await load()
      uploadDismissTimerRef.current = window.setTimeout(() => { if (generation === uploadGenerationRef.current) setUpload(null) }, 900)
    } catch (caught) {
      if (generation === uploadGenerationRef.current) setUpload({ progress: 0, error: caught instanceof Error ? caught.message : 'Upload failed.' })
    } finally { uploadActiveRef.current = false }
  }

  const navigate = (next: View) => {
    setView(next)
    setError('')
    setMobileNavOpen(false)
    if (next !== 'detail') setSeekSeconds(null)
  }

  const filtered = useMemo(() => records.filter((recording) => {
    if (!recording.title.toLowerCase().includes(query.toLowerCase())) return false
    if (statusFilter !== 'all' && recording.transcription_status !== statusFilter) return false
    if (dateFilter === 'all') return true
    const days = dateFilter === 'week' ? 7 : 31
    return Date.now() - parseDateValue(recording.created_at).getTime() <= days * 86400000
  }), [records, query, statusFilter, dateFilter])

  const nav: [View, string][] = [
    ['dashboard', 'Archive'],
    ['recordings', 'Recordings'],
    ['search', 'Memory search'],
    ['settings', 'About'],
  ]

  return <div className="app">
    {mobileNavOpen && <button className="nav-scrim" aria-label="Close navigation" onClick={() => setMobileNavOpen(false)} />}
    <aside ref={mobileNavRef} className={`sidebar ${mobileNavOpen ? 'mobile-open' : ''}`} aria-label="Primary navigation">
      <button className="brand" onClick={() => navigate('dashboard')}>
        <span className="seal"><AudioLines size={19} /></span>
        <span>Last<span>Whisper</span></span>
      </button>
      <nav>{nav.map(([target, label]) => {
        const Icon = target === 'dashboard' ? Archive : target === 'recordings' ? FileAudio : target === 'search' ? Search : Settings
        return <button key={target} className={view === target ? 'active' : ''} aria-current={view === target ? 'page' : undefined} onClick={() => navigate(target)}>
          <Icon size={18} />{label}
        </button>
      })}</nav>
      <div className="sidebar-foot"><span className="privacy-dot" /> Local-first archive</div>
    </aside>
    <main>
      <header>
        <button ref={mobileMenuRef} className="mobile-menu" aria-label="Open navigation" aria-expanded={mobileNavOpen} onClick={() => setMobileNavOpen((open) => !open)}><Menu /></button>
        <div className="eyebrow">PRIVATE MEMORY ARCHIVE</div>
        <button className="import top-import" onClick={() => fileRef.current?.click()}><Plus size={18} /> Import audio</button>
      </header>
      <input
        ref={fileRef}
        type="file"
        accept="audio/wav,audio/mpeg,audio/mp4,audio/flac,.wav,.mp3,.m4a,.flac"
        hidden
        onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void uploadFile(file) }}
      />
      {upload && <div className={`notice ${upload.error ? 'bad' : ''}`} role={upload.error ? 'alert' : 'status'} aria-live="polite">
        {upload.error || <><LoaderCircle size={17} className={upload.progress < 100 ? 'spin' : ''} />{upload.progress === 100 ? 'Saved to your archive.' : `Importing audio… ${upload.progress}%`}</>}
        <button onClick={() => setUpload(null)} aria-label="Dismiss"><X size={16} /></button>
      </div>}
      {error && <div className="notice bad" role="alert">{error}<button onClick={() => records.length ? setError('') : void load()}>{records.length ? 'Dismiss' : 'Retry archive'}</button></div>}
      {backgroundError && <div className="notice bad" role="alert">{backgroundError}<button onClick={() => void load(true)}>Retry updates</button></div>}
      {!loading && <div className={view === 'search' ? '' : 'view-hidden'} aria-hidden={view !== 'search'}><MemorySearch onOpenRecording={openSource} onMemoriesChanged={refreshStats} /></div>}
      {loading
        ? <div className="center" role="status" aria-live="polite"><LoaderCircle className="spin" /><p>Opening your archive…</p></div>
        : error && records.length === 0
          ? <div className="empty"><div className="empty-icon"><X /></div><h3>Archive unavailable</h3><p>LastWhisper could not reach the configured local API.</p><button className="import" onClick={() => void load()}>Retry connection</button></div>
          : view === 'dashboard'
          ? <Dashboard records={records} stats={stats} onImport={() => fileRef.current?.click()} onOpen={(recording) => open(recording)} onLibrary={() => navigate('recordings')} />
          : view === 'recordings'
            ? <Library records={filtered} hasArchiveItems={records.length > 0} query={query} setQuery={setQuery} statusFilter={statusFilter} setStatusFilter={setStatusFilter} dateFilter={dateFilter} setDateFilter={setDateFilter} grid={grid} setGrid={setGrid} onOpen={(recording) => open(recording)} onImport={() => fileRef.current?.click()} />
            : view === 'detail' && selected
              ? <Detail
                record={records.find((item) => item.id === selected.id) ?? selected}
                initialSeekSeconds={seekSeconds}
                onRecordingChanged={(recording) => {
                  setRecords((items) => items.map((item) => item.id === recording.id ? recording : item))
                  setSelected(recording)
                  if (!['pending', 'processing'].includes(recording.transcription_status)) void refreshStats()
                }}
                backLabel={detailReturnView === 'search' ? 'Back to memory search' : 'Back to recordings'}
                onBack={() => navigate(detailReturnView)}
                onRename={async (title) => {
                  const recording = await api.rename(selected.id, title)
                  setRecords((items) => items.map((item) => item.id === recording.id ? recording : item))
                  setSelected(recording)
                }}
                onDelete={async () => {
                  if (!window.confirm('Delete this recording, its index, saved memories, and local audio file?')) return
                  await api.remove(selected.id)
                  setRecords((items) => items.filter((item) => item.id !== selected.id))
                  setView('recordings')
                  await load()
                }}
              />
              : view === 'search'
                ? null
                : <SettingsPage />}
    </main>
  </div>
}

function Dashboard({ records, stats, onImport, onOpen, onLibrary }: {
  records: Recording[]
  stats: Stats | null
  onImport: () => void
  onOpen: (recording: Recording) => void
  onLibrary: () => void
}) {
  const metrics = [
    ['Total recordings', stats?.total_recordings ?? 0, FileAudio],
    ['Transcribed', stats?.transcribed_recordings ?? 0, BookOpen],
    ['Transcribed duration', duration(stats?.total_duration_seconds ?? 0), AudioLines],
    ['Saved memories', stats?.saved_memories ?? 0, Sparkles],
  ] as const
  return <section>
    <div className="hero">
      <div><h1>Your memories,<br /><em>never lost.</em></h1><p>A private place for the stories, thoughts, and small moments worth keeping.</p></div>
      <button className="import hero-import" onClick={onImport}><Upload size={18} /> Add a recording</button>
    </div>
    <div className="metrics">{metrics.map(([label, value, Icon]) => <article key={label}><Icon size={18} /><strong>{value}</strong><span>{label}</span></article>)}</div>
    <div className="section-heading">
      <div><div className="eyebrow">RECENTLY ADDED</div><h2>Recordings</h2></div>
      {records.length > 0 && <button className="text-button" onClick={onLibrary}>View library →</button>}
    </div>
    {records.length === 0
      ? <Empty onImport={onImport} />
      : <div className="record-list">{records.slice(0, 4).map((recording) => <RecordRow key={recording.id} record={recording} onOpen={() => onOpen(recording)} />)}</div>}
  </section>
}

function Empty({ onImport }: { onImport: () => void }) {
  return <div className="empty">
    <div className="empty-icon"><Archive /></div>
    <h3>Your archive is waiting</h3>
    <p>Bring in the first voice note, interview, or family story. By default, it stays with your local LastWhisper backend.</p>
    <button className="import" onClick={onImport}><Upload size={17} /> Import your first recording</button>
  </div>
}

function RecordRow({ record, onOpen }: { record: Recording; onOpen: () => void }) {
  return <button className="record-row" onClick={onOpen}>
    <span className="file-icon"><FileAudio size={19} /></span>
    <span className="record-title"><b>{record.title}</b><small>{formatDate(record.created_at)} · {duration(record.duration_seconds)}</small></span>
    <Wave /><Status status={record.transcription_status} /><span>›</span>
  </button>
}

function Library({ records, hasArchiveItems, query, setQuery, statusFilter, setStatusFilter, dateFilter, setDateFilter, grid, setGrid, onOpen, onImport }: {
  records: Recording[]
  hasArchiveItems: boolean
  query: string
  setQuery: (value: string) => void
  statusFilter: string
  setStatusFilter: (value: string) => void
  dateFilter: string
  setDateFilter: (value: string) => void
  grid: boolean
  setGrid: (value: boolean) => void
  onOpen: (recording: Recording) => void
  onImport: () => void
}) {
  return <section>
    <div className="page-title">
      <div><div className="eyebrow">THE COLLECTION</div><h1>Recordings</h1><p>Recordings stored by your configured LastWhisper backend.</p></div>
      <button className="import" onClick={onImport}><Upload size={17} /> Import audio</button>
    </div>
    <div className="toolbar">
      <label className="search"><Search size={17} /><span className="sr-only">Search recording titles</span><input aria-label="Search recording titles" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search recording titles" /></label>
      <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} aria-label="Filter transcription status">
        <option value="all">All statuses</option><option value="not_started">Awaiting transcription</option><option value="pending">Queued</option><option value="processing">Transcribing</option><option value="cancelling">Cancelling</option><option value="complete">Transcribed</option><option value="failed">Needs attention</option><option value="cancelled">Cancelled</option>
      </select>
      <select value={dateFilter} onChange={(event) => setDateFilter(event.target.value)} aria-label="Filter date">
        <option value="all">Any time</option><option value="week">Past 7 days</option><option value="month">Past 31 days</option>
      </select>
      <span className="view-toggle">
        <button className={!grid ? 'selected' : ''} aria-pressed={!grid} onClick={() => setGrid(false)} aria-label="List view"><LayoutList size={17} /></button>
        <button className={grid ? 'selected' : ''} aria-pressed={grid} onClick={() => setGrid(true)} aria-label="Grid view"><Grid2X2 size={17} /></button>
      </span>
    </div>
    {records.length === 0
      ? hasArchiveItems
        ? <div className="empty"><div className="empty-icon"><Search /></div><h3>No recordings match</h3><p>Clear or adjust the title, status, and date filters.</p></div>
        : <Empty onImport={onImport} />
      : <div className={grid ? 'record-grid' : 'record-list'}>{records.map((recording) => grid
        ? <button className="record-card" key={recording.id} onClick={() => onOpen(recording)}><Wave /><Status status={recording.transcription_status} /><h3>{recording.title}</h3><p>{formatDate(recording.created_at)} · {duration(recording.duration_seconds)}</p></button>
        : <RecordRow key={recording.id} record={recording} onOpen={() => onOpen(recording)} />)}</div>}
  </section>
}

function Detail({ record, initialSeekSeconds, onRecordingChanged, backLabel, onBack, onRename, onDelete }: {
  record: Recording
  initialSeekSeconds: number | null
  onRecordingChanged: (recording: Recording) => void
  backLabel: string
  onBack: () => void
  onRename: (title: string) => Promise<void>
  onDelete: () => Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(record.title)
  const [busy, setBusy] = useState(false)
  const [current, setCurrent] = useState(record)
  const [find, setFind] = useState('')
  const [actionError, setActionError] = useState('')
  const [pollError, setPollError] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [playerError, setPlayerError] = useState('')
  const [detailLoading, setDetailLoading] = useState(true)
  const audioRef = useRef<HTMLAudioElement>(null)

  const refresh = async () => {
    try {
      const updated = await api.get(record.id)
      setCurrent(updated)
      onRecordingChanged(updated)
      setPollError('')
    } catch (caught) {
      setPollError(caught instanceof Error ? caught.message : 'Live updates paused.')
    } finally { setDetailLoading(false) }
  }

  useEffect(() => {
    setDetailLoading(true)
    setCurrent(record)
    setTitle(record.title)
    void refresh()
  }, [record.id])

  useEffect(() => {
    const shouldPoll = ['pending', 'processing', 'cancelling'].includes(current.transcription_status) || current.extraction_status === 'pending'
    if (!shouldPoll) return
    let stopped = false
    let timer = 0
    const poll = async () => {
      await refresh()
      if (!stopped) timer = window.setTimeout(() => void poll(), 2000)
    }
    timer = window.setTimeout(() => void poll(), 2000)
    return () => { stopped = true; window.clearTimeout(timer) }
  }, [current.transcription_status, current.extraction_status, record.id])

  useEffect(() => {
    if (initialSeekSeconds == null || !audioRef.current) return
    const audio = audioRef.current
    const applySeek = () => { audio.currentTime = initialSeekSeconds }
    if (audio.readyState >= 1) applySeek()
    else audio.addEventListener('loadedmetadata', applySeek, { once: true })
    return () => audio.removeEventListener('loadedmetadata', applySeek)
  }, [current.id, initialSeekSeconds])

  const start = async () => {
    const replacing = current.transcription_status === 'complete' || Boolean(current.segments?.length || current.transcript_text)
    if (replacing && !window.confirm('Re-transcribing replaces this transcript, its search index, summary, and any saved memories linked to the old source segments. Continue?')) return
    setBusy(true); setActionError('')
    try {
      await api.transcribe(current.id, undefined, replacing)
      await refresh()
    }
    catch (caught) { setActionError(caught instanceof Error ? caught.message : 'Could not start transcription.') }
    finally { setBusy(false) }
  }

  const cancel = async () => {
    setBusy(true); setActionError('')
    try {
      await api.cancel(current.id)
      await refresh()
    }
    catch (caught) { setActionError(caught instanceof Error ? caught.message : 'Could not cancel transcription.') }
    finally { setBusy(false) }
  }

  const seek = (time: number) => {
    if (!audioRef.current) return
    audioRef.current.currentTime = time
    setPlayerError('')
    void audioRef.current.play().catch(() => setPlayerError('Playback could not start. Use the player controls to retry.'))
  }

  const remove = async () => {
    setDeleting(true); setActionError('')
    try { await onDelete() }
    catch (caught) { setActionError(caught instanceof Error ? caught.message : 'Could not delete this recording.') }
    finally { setDeleting(false) }
  }

  const segments = (current.segments ?? []).filter((segment) => segment.text.toLowerCase().includes(find.toLowerCase()))
  const extracted = parseExtractedMemories(current.extracted_memories)

  return <section>
    <button className="back" onClick={onBack}><ChevronLeft size={17} /> {backLabel}</button>
    <div className="detail-head">
      <div>{editing
        ? <form onSubmit={async (event) => {
          event.preventDefault(); setBusy(true); setActionError('')
          try { await onRename(title.trim()); setCurrent((value) => ({ ...value, title: title.trim() })); setEditing(false) }
          catch (caught) { setActionError(caught instanceof Error ? caught.message : 'Could not rename this recording.') }
          finally { setBusy(false) }
        }}>
          <input aria-label="Recording title" className="title-edit" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={255} autoFocus />
          <button className="text-button" disabled={busy || deleting || !title.trim()}>Save</button>
          <button type="button" className="text-button" disabled={busy || deleting} onClick={() => { setTitle(current.title); setEditing(false) }}>Cancel</button>
        </form>
        : <><div className="eyebrow">RECORDING</div><h1>{current.title}</h1><p>{formatDate(current.created_at)} · {current.original_filename} · {duration(current.duration_seconds)}</p><button className="text-button" disabled={busy || deleting || detailLoading} onClick={() => setEditing(true)}>Rename</button></>}
      </div>
      <button className="danger" disabled={busy || deleting || detailLoading || ['pending', 'processing', 'cancelling'].includes(current.transcription_status)} onClick={() => void remove()}><Trash2 size={16} /> {deleting ? 'Deleting…' : 'Delete'}</button>
    </div>
    {detailLoading && <div className="notice" role="status" aria-live="polite"><LoaderCircle size={16} className="spin" /> Loading complete recording details…</div>}
    {actionError && <div className="notice bad" role="alert">{actionError}<button aria-label="Dismiss error" onClick={() => setActionError('')}><X size={16} /></button></div>}
    {pollError && <div className="notice bad" role="alert">{pollError}<button onClick={() => void refresh()}>Retry updates</button></div>}
    {initialSeekSeconds != null && <p className="source-jump"><Clock size={13} /> Opened at source timestamp {duration(initialSeekSeconds)}</p>}
    <div className="player real-player"><audio ref={audioRef} controls preload="metadata" src={api.audioUrl(current.id)} onError={() => setPlayerError('The local audio file could not be loaded.')}>Your browser does not support audio playback.</audio></div>
    {playerError && <p className="error-copy" role="alert">{playerError}</p>}
    <div className="transcribe-card" role="status" aria-live="polite">
      <div>
        <div className="eyebrow">LOCAL TRANSCRIPTION</div>
        <b>{current.transcription_status === 'complete' ? 'Transcription complete' : current.transcription_status === 'cancelling' ? 'Cancelling local transcription…' : ['processing', 'pending'].includes(current.transcription_status) ? `Transcribing locally… ${current.transcription_progress}%` : current.transcription_status === 'failed' ? 'Transcription failed' : 'Ready for local transcription'}</b>
        <p>{current.selected_language ? `Requested language: ${current.selected_language}. ` : ''}{current.transcription_language ? `Detected language: ${current.transcription_language}.` : current.transcription_status === 'not_started' ? 'Uses the configured local Whisper model; language is detected automatically.' : ''}</p>
        {current.error_message && <p className="error-copy">{current.error_message}</p>}
      </div>
      {['pending', 'processing', 'cancelling'].includes(current.transcription_status)
        ? <button className="danger" disabled={busy || current.transcription_status === 'cancelling'} onClick={() => void cancel()}>{current.transcription_status === 'cancelling' ? 'Cancelling…' : 'Cancel'}</button>
        : <button className="import" disabled={busy || deleting || detailLoading} onClick={() => void start()}>{current.transcription_status === 'complete' || current.segments?.length ? 'Re-transcribe' : current.transcription_status === 'failed' || current.transcription_status === 'cancelled' ? 'Retry transcription' : 'Start transcription'}</button>}
    </div>
    <div className="detail-grid">
      <article>
        <div className="eyebrow">TRANSCRIPT</div><h2>Transcript</h2>
        {current.segments?.length
          ? <><label className="search transcript-search"><Search size={16} /><span className="sr-only">Search this transcript</span><input aria-label="Search this transcript" value={find} onChange={(event) => setFind(event.target.value)} placeholder="Search this transcript" /></label>{segments.length ? <div className="segments">{segments.map((segment) => <button key={segment.id} className="segment" onClick={() => seek(segment.start_seconds)}><time>{duration(segment.start_seconds)}</time><span>{segment.text}</span></button>)}</div> : <div className="panel-empty compact"><Search /><p>No transcript matches</p><small>Try a different word or phrase.</small></div>}</>
          : <div className="panel-empty"><BookOpen /><p>{current.transcription_status === 'complete' ? 'No speech segments were saved.' : 'No transcript yet.'}</p><small>Start transcription with the configured local LastWhisper backend.</small></div>}
      </article>
      <article>
        <div className="eyebrow">LOCAL ENRICHMENT</div><h2>Summary & memories</h2>
        {current.extraction_status === 'complete' && current.summary
          ? <div className="memories"><p>{current.summary}</p>{extracted?.key_topics?.length ? <><h3>Topics</h3><p>{extracted.key_topics.join(' · ')}</p></> : null}{(['people', 'places', 'dates', 'action_items'] as const).map((key) => extracted?.[key]?.length ? <div key={key}><h3>{key.replace('_', ' ')}</h3>{extracted[key]!.map((item) => <button className="entity" key={item.value} onClick={() => { const segment = current.segments?.find((candidate) => item.segment_ids.includes(candidate.id)); if (segment) seek(segment.start_seconds) }}>{item.value}</button>)}</div> : null)}</div>
          : <div className="panel-empty"><Sparkles /><p>{current.extraction_status === 'pending' ? 'Local summary is being prepared.' : current.segments?.length ? 'Summary unavailable' : 'Transcribe this recording first'}</p><small>{current.segments?.length ? current.extraction_status === 'unavailable' ? 'Optional summaries require a configured local Ollama model.' : 'Only transcript-supported details are kept.' : 'Summaries can only be created from a completed transcript.'}</small></div>}
      </article>
    </div>
  </section>
}

function MemorySearch({ onOpenRecording, onMemoriesChanged }: {
  onOpenRecording: (recordingId: string, atSeconds: number) => Promise<void>
  onMemoriesChanged: () => Promise<void>
}) {
  type Tab = 'search' | 'saved'
  const [tab, setTab] = useState<Tab>('search')
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [results, setResults] = useState<SearchResult[]>([])
  const [searchError, setSearchError] = useState('')
  const [hasSearched, setHasSearched] = useState(false)
  const [submittedQuery, setSubmittedQuery] = useState('')
  const [scoreExplanation, setScoreExplanation] = useState('')
  const [memories, setMemories] = useState<SavedMemory[]>([])
  const [memoryLoading, setMemoryLoading] = useState(false)
  const [memoryError, setMemoryError] = useState('')
  const [index, setIndex] = useState<IndexStatus | null>(null)
  const [rebuilding, setRebuilding] = useState(false)
  const [rebuildMessage, setRebuildMessage] = useState('')
  const [savingChunk, setSavingChunk] = useState<string | null>(null)
  const [editId, setEditId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editNote, setEditNote] = useState('')
  const [savingEdit, setSavingEdit] = useState(false)
  const searchGeneration = useRef(0)
  const memoryLoadGeneration = useRef(0)

  const loadStatus = async () => {
    try { setIndex(await api.searchStatus()) }
    catch (caught) { setRebuildMessage(caught instanceof Error ? caught.message : 'Could not read search-index status.') }
  }

  const loadMemories = async () => {
    const generation = ++memoryLoadGeneration.current
    setMemoryLoading(true); setMemoryError('')
    try { const loaded = await api.memories(); if (generation === memoryLoadGeneration.current) setMemories(loaded) }
    catch (caught) { if (generation === memoryLoadGeneration.current) setMemoryError(caught instanceof Error ? caught.message : 'Failed to load saved memories.') }
    finally { if (generation === memoryLoadGeneration.current) setMemoryLoading(false) }
  }

  useEffect(() => { void loadStatus(); void loadMemories() }, [])

  useEffect(() => {
    if (!index || (!index.rebuilding && index.pending_recordings === 0)) return
    let stopped = false
    let timer = 0
    const poll = async () => {
      await loadStatus()
      if (!stopped) timer = window.setTimeout(() => void poll(), 2000)
    }
    timer = window.setTimeout(() => void poll(), 2000)
    return () => { stopped = true; window.clearTimeout(timer) }
  }, [index?.rebuilding, index?.pending_recordings])

  const doSearch = async (event?: React.FormEvent) => {
    event?.preventDefault()
    const submitted = query.trim()
    if (submitted.length < 2 || rebuilding || index?.rebuilding) return
    const generation = ++searchGeneration.current
    setSubmittedQuery(submitted)
    setSearching(true); setSearchError(''); setHasSearched(true)
    try {
      const response = await api.search(submitted)
      if (generation !== searchGeneration.current) return
      setResults(response.results)
      setScoreExplanation(response.score_explanation)
      await loadStatus()
    } catch (caught) {
      if (generation !== searchGeneration.current) return
      setSearchError(caught instanceof Error ? caught.message : 'Search failed.')
      setResults([])
    } finally { if (generation === searchGeneration.current) setSearching(false) }
  }

  const doRebuild = async () => {
    if (searching || savingChunk) return
    searchGeneration.current += 1
    setRebuilding(true); setRebuildMessage('')
    try {
      const result = await api.rebuildIndex()
      const failures = result.errors.length ? ` ${result.errors.map((error) => `${error.recording_id.slice(0, 8)}: ${error.error}`).join(' ')}` : ''
      setRebuildMessage(`Indexed ${result.indexed_chunks} chunks across ${result.indexed_recordings} recordings.${failures}`)
      setResults([]); setHasSearched(false); setSubmittedQuery('')
      await loadStatus()
    } catch (caught) {
      setRebuildMessage(caught instanceof Error ? caught.message : 'Rebuild failed.')
    } finally { setRebuilding(false) }
  }

  const saveResult = async (result: SearchResult) => {
    if (rebuilding || index?.rebuilding) return
    setSavingChunk(result.chunk_id); setMemoryError('')
    try {
      const memory = await api.saveMemory({ search_chunk_id: result.chunk_id })
      memoryLoadGeneration.current += 1
      setMemories((items) => [memory, ...items.filter((item) => item.id !== memory.id)])
      await onMemoriesChanged()
    } catch (caught) {
      setMemoryError(caught instanceof Error ? caught.message : 'Could not save this memory.')
    } finally { setSavingChunk(null) }
  }

  const startEdit = (memory: SavedMemory) => {
    if (savingEdit) return
    setEditId(memory.id); setEditTitle(memory.title ?? ''); setEditNote(memory.note ?? ''); setMemoryError('')
  }

  const cancelEdit = () => { setEditId(null); setEditTitle(''); setEditNote('') }

  const saveEdit = async () => {
    if (!editId) return
    const memoryId = editId
    setSavingEdit(true); setMemoryError('')
    try {
      const updated = await api.updateMemory(memoryId, { title: editTitle.trim() || null, note: editNote.trim() || null })
      setMemories((items) => items.map((item) => item.id === updated.id ? updated : item))
      if (editId === memoryId) cancelEdit()
    } catch (caught) {
      setMemoryError(caught instanceof Error ? caught.message : 'Could not update this memory.')
    } finally { setSavingEdit(false) }
  }

  const unsave = async (id: string) => {
    if (!window.confirm('Remove this saved memory?')) return
    setMemoryError('')
    try {
      await api.deleteMemory(id)
      setMemories((items) => items.filter((item) => item.id !== id))
      await onMemoriesChanged()
    } catch (caught) {
      setMemoryError(caught instanceof Error ? caught.message : 'Could not remove this memory.')
    }
  }

  const selectTab = (next: Tab) => {
    setTab(next)
    if (next === 'saved') void loadMemories()
  }

  const handleTabKey = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return
    event.preventDefault()
    const next: Tab = event.key === 'ArrowRight' ? 'saved' : 'search'
    selectTab(next)
    document.getElementById(`memory-${next}-tab`)?.focus()
  }

  const savedSegments = new Set(memories.map((memory) => memory.segment_id))
  const modelLabel = index?.model_status === 'loaded'
    ? 'Embedding model loaded locally'
    : index?.model_status === 'not_loaded'
      ? 'Model weights are checked on first search or rebuild'
      : index?.error ?? 'Embedding model unavailable'

  return <section className="search-page">
    <div className="eyebrow">SEMANTIC SEARCH</div>
    <h1>Ask your archive</h1>
    <p>Find a thought even when you cannot remember the exact words. The default localhost setup embeds queries and transcripts on this computer; model files may download on first use.</p>
    {index && <div className={`idx-status ${['unavailable', 'error'].includes(index.model_status) ? 'unavailable' : ''}`}>
      {index.model_status === 'loaded' ? <Check size={14} /> : index.model_status === 'not_loaded' ? <Clock size={14} /> : <X size={14} />}
      <span><b>{modelLabel}</b><small>{index.total_chunks} chunks across {index.indexed_recordings} recordings · {index.configured_device}{index.pending_recordings ? ` · ${index.pending_recordings} awaiting indexing` : ''}{index.failed_recordings ? ` · ${index.failed_recordings} failed` : ''}</small></span>
      <button className="text-button" onClick={() => void doRebuild()} disabled={rebuilding || index.rebuilding || searching || Boolean(savingChunk)}><RefreshCw size={13} className={rebuilding || index.rebuilding ? 'spin' : ''} /> {rebuilding || index.rebuilding ? 'Rebuilding…' : 'Rebuild index'}</button>
    </div>}
    {rebuildMessage && <div className="notice" role="status">{rebuildMessage}<button onClick={() => setRebuildMessage('')} aria-label="Dismiss"><X size={16} /></button></div>}
    {memoryError && <div className="notice bad" role="alert">{memoryError}<button onClick={() => setMemoryError('')} aria-label="Dismiss"><X size={16} /></button></div>}
    <div className="search-tabs" role="tablist" aria-label="Memory search views">
      <button id="memory-search-tab" role="tab" aria-selected={tab === 'search'} tabIndex={tab === 'search' ? 0 : -1} className={tab === 'search' ? 'active' : ''} onKeyDown={handleTabKey} onClick={() => selectTab('search')}><Search size={15} /> Search</button>
      <button id="memory-saved-tab" role="tab" aria-selected={tab === 'saved'} tabIndex={tab === 'saved' ? 0 : -1} className={tab === 'saved' ? 'active' : ''} onKeyDown={handleTabKey} onClick={() => selectTab('saved')}><Bookmark size={15} /> Saved memories{memories.length > 0 && <span className="tab-count">{memories.length}</span>}</button>
    </div>
    {tab === 'search' ? <>
      <form className="big-search" onSubmit={doSearch}>
        <Search size={20} />
        <label className="sr-only" htmlFor="memory-query">Search your transcript memories</label>
        <input id="memory-query" aria-label="Search your transcript memories" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="What did I say about changing careers?" maxLength={500} />
        <button type="submit" className="import" disabled={searching || rebuilding || index?.rebuilding || query.trim().length < 2}>{searching ? <LoaderCircle size={16} className="spin" /> : 'Search'}</button>
      </form>
      {searchError && <div className="notice bad" role="alert">{searchError}<button aria-label="Dismiss search error" onClick={() => setSearchError('')}><X size={16} /></button></div>}
      {searching
        ? <div className="center search-loading" role="status" aria-live="polite"><LoaderCircle className="spin" /><p>Searching your local archive…</p></div>
        : results.length > 0
          ? <><p className="score-note">Results for “{submittedQuery}”. {scoreExplanation}</p><div className="search-results">{results.map((result) => {
            const saved = savedSegments.has(result.segment_id)
            return <article key={result.chunk_id} className="result-card">
              <div className="result-head">
                <div><b>{result.recording_title}</b><small>{formatDate(result.recording_date)} · {duration(result.start_seconds)}–{duration(result.end_seconds)}</small></div>
                <div className="result-actions">
                  <span className="similarity" title="Cosine similarity ranking signal, not a probability">score {result.similarity.toFixed(3)}</span>
                  <button className="text-button" disabled={saved || rebuilding || index?.rebuilding || savingChunk === result.chunk_id} onClick={() => void saveResult(result)}>{savingChunk === result.chunk_id ? <LoaderCircle size={14} className="spin" /> : <Bookmark size={14} />}{saved ? 'Saved' : 'Save'}</button>
                  <button className="text-button" onClick={() => void onOpenRecording(result.recording_id, result.start_seconds)}><ExternalLink size={14} /> Open source</button>
                </div>
              </div>
              <p className="result-text">{result.text}</p>
            </article>
          })}</div></>
          : hasSearched && !searchError
            ? <div className="empty"><div className="empty-icon"><Search /></div><h3>No matching evidence</h3><p>Try another phrasing, or rebuild the index after transcribing new recordings.</p></div>
            : <div className="phase-note"><Sparkles /><div><h3>Evidence only</h3><p>Results quote your transcripts and link back to exact source timestamps. LastWhisper does not invent an answer when evidence is missing.</p></div></div>}
    </> : <>
      {memoryLoading
        ? <div className="center search-loading" role="status" aria-live="polite"><LoaderCircle className="spin" /><p>Loading saved memories…</p></div>
        : memories.length > 0
          ? <div className="saved-memories">{memories.map((memory) => <article key={memory.id} className="memory-card">
            {editId === memory.id
              ? <form className="memory-edit" onSubmit={(event) => { event.preventDefault(); void saveEdit() }}>
                <label htmlFor={`memory-title-${memory.id}`}>Title</label><input id={`memory-title-${memory.id}`} value={editTitle} onChange={(event) => setEditTitle(event.target.value)} placeholder="Title" maxLength={255} className="mem-title-input" />
                <label htmlFor={`memory-note-${memory.id}`}>Personal note</label><textarea id={`memory-note-${memory.id}`} value={editNote} onChange={(event) => setEditNote(event.target.value)} placeholder="Personal note…" maxLength={4000} rows={3} className="mem-note-input" />
                <div className="mem-edit-actions"><button className="import" type="submit" disabled={savingEdit}>{savingEdit ? <LoaderCircle size={14} className="spin" /> : 'Save'}</button><button type="button" className="text-button" disabled={savingEdit} onClick={cancelEdit}>Cancel</button></div>
              </form>
              : <>
                <div className="memory-head">
                  <div><b>{memory.title || `${memory.source_text.slice(0, 60)}${memory.source_text.length > 60 ? '…' : ''}`}</b><small><Clock size={12} /> Saved {formatDate(memory.created_at)} · from <em>{memory.recording_title}</em></small></div>
                  <div className="memory-actions">
                    <button className="text-button" aria-label="Edit saved memory" onClick={() => startEdit(memory)}><Edit3 size={14} /></button>
                    <button className="text-button" aria-label="Open source recording" onClick={() => void onOpenRecording(memory.recording_id, memory.source_start_seconds)}><ExternalLink size={14} /></button>
                    <button className="text-button danger-text" aria-label="Unsave memory" onClick={() => void unsave(memory.id)}><Trash2 size={14} /></button>
                  </div>
                </div>
                <p className="memory-text">“{memory.source_text}”</p>
                <div className="memory-meta"><span><Clock size={11} /> {duration(memory.source_start_seconds)}–{duration(memory.source_end_seconds)}</span><span>{formatDate(memory.recording_date)}</span></div>
                {memory.note && <p className="memory-note">{memory.note}</p>}
              </>}
          </article>)}</div>
          : !memoryError && <div className="empty"><div className="empty-icon"><Bookmark /></div><h3>No saved memories yet</h3><p>Search your archive and save transcript evidence you want to revisit.</p></div>}
    </>}
  </section>
}

function SettingsPage() {
  return <section>
    <div className="eyebrow">LOCAL CONFIGURATION</div><h1>About</h1><p className="intro">A quiet, local-first archive by design. Runtime settings are managed in the backend environment file.</p>
    <div className="settings">
      <article><Archive /><div><h2>Private by default</h2><p>The default localhost setup keeps audio, transcripts, embeddings, and SQLite data on this computer. Do not expose the API to a network without adding authentication.</p></div></article>
      <article><AudioLines /><div><h2>Local transcription</h2><p>Whisper runs through faster-whisper. Model weights download on first use unless a local cache is configured and offline-only mode is enabled.</p></div></article>
      <article><Search /><div><h2>Semantic memory search</h2><p>Sentence-transformer inference runs in the backend and normalized embeddings are stored in SQLite. Initial model retrieval may require internet access.</p></div></article>
      <article><Settings /><div><h2>Optional summaries</h2><p>Ollama summaries are disabled unless a model is configured. Remote Ollama endpoints are rejected by default because they would receive transcript text.</p></div></article>
    </div>
  </section>
}

export default App

const root = document.getElementById('root')
if (!root) throw new Error('LastWhisper could not find its application root.')
createRoot(root).render(<App />)
