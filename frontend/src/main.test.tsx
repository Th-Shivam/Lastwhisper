import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>'
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const payload = url.endsWith('/recordings')
      ? []
      : url.endsWith('/recordings/stats')
        ? { total_recordings: 0, transcribed_recordings: 0, total_duration_seconds: 0, saved_memories: 0 }
        : url.endsWith('/search/status')
          ? { available: false, model_status: 'not_loaded', model_loaded: false, configured_model: 'local-model', configured_device: 'cpu', total_chunks: 0, indexed_recordings: 0, pending_recordings: 0, failed_recordings: 0, rebuilding: false, error: null }
          : url.endsWith('/memories')
            ? []
            : { status: 'ok' }
    return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.resetModules()
})

test('mounts the archive and opens mobile navigation', async () => {
  await import('./main')
  expect(await screen.findByRole('heading', { name: /your memories/i })).toBeInTheDocument()
  const menu = screen.getByRole('button', { name: /open navigation/i })
  fireEvent.click(menu)
  await waitFor(() => expect(screen.getByLabelText('Primary navigation')).toHaveClass('mobile-open'))
  expect(menu).toHaveAttribute('aria-expanded', 'true')
})

test('opens semantic evidence at its source audio timestamp', async () => {
  const recording = {
    id: 'recording-1', title: 'Project note', original_filename: 'project.wav',
    mime_type: 'audio/wav', file_size: 100, duration_seconds: 12,
    created_at: '2026-10-30T00:00:00Z', transcription_status: 'complete',
    transcript_text: 'I discussed my project roadmap.', summary: null,
    error_message: null, transcription_language: 'en', selected_language: null,
    transcription_progress: 100, extraction_status: 'unavailable', extracted_memories: null,
    segments: [{ id: 'segment-1', position: 0, start_seconds: 4, end_seconds: 8, text: 'I discussed my project roadmap.' }],
  }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const payload = url.endsWith('/recordings') ? [recording]
      : url.endsWith('/recordings/stats') ? { total_recordings: 1, transcribed_recordings: 1, total_duration_seconds: 12, saved_memories: 0 }
        : url.endsWith('/search/status') ? { available: true, model_status: 'loaded', model_loaded: true, configured_model: 'local-model', configured_device: 'cpu', total_chunks: 1, indexed_recordings: 1, pending_recordings: 0, failed_recordings: 0, rebuilding: false, error: null }
          : url.endsWith('/memories') ? []
            : url.endsWith('/search') ? { minimum_similarity: 0.3, score_explanation: 'Ranking signal.', results: [{ chunk_id: 'chunk-1', recording_id: recording.id, recording_title: recording.title, recording_date: recording.created_at, segment_id: 'segment-1', segment_ids: ['segment-1'], start_seconds: 4, end_seconds: 8, text: recording.transcript_text, similarity: 0.9 }] }
              : url.endsWith(`/recordings/${recording.id}`) ? recording
                : { status: 'ok' }
    return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }))
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()

  await import('./main')
  fireEvent.click(await screen.findByRole('button', { name: 'Memory search' }))
  fireEvent.change(screen.getByLabelText('Search your transcript memories'), { target: { value: 'project roadmap' } })
  const searchButtons = screen.getAllByRole('button', { name: 'Search' })
  fireEvent.click(searchButtons[searchButtons.length - 1])
  fireEvent.click(await screen.findByRole('button', { name: /open source/i }))
  await screen.findByRole('heading', { name: 'Project note' })
  const audio = document.querySelector('audio') as HTMLAudioElement
  expect(audio).not.toBeNull()
  fireEvent.loadedMetadata(audio)
  await waitFor(() => expect(audio.currentTime).toBe(4))
})
