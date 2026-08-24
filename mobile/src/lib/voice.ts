/**
 * Voice goes through the engine's host, never straight to ElevenLabs: the key
 * stays server-side, so nothing sensitive ships inside the app bundle.
 *
 * Speech is an *utterance*. It reaches the agent with exactly the standing that
 * typing has — the engine still decides, and no verdict is reachable by voice
 * that is not reachable by text.
 */
import {
  AudioModule,
  RecordingPresets,
  createAudioPlayer,
  setAudioModeAsync,
  type AudioPlayer,
  type AudioRecorder,
} from 'expo-audio';
import { File, Paths } from 'expo-file-system';

import { API_BASE } from '@/lib/api';

export const RECORDING_OPTIONS = RecordingPresets.HIGH_QUALITY;

/** Ask for the mic the first time the user reaches for it, then start. */
export async function startListening(recorder: AudioRecorder): Promise<void> {
  const { granted } = await AudioModule.requestRecordingPermissionsAsync();
  if (!granted) throw new Error('Microphone access is off for this app.');
  await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
  await recorder.prepareToRecordAsync();
  recorder.record();
}

/** Stop, upload the clip as raw bytes, and return what was said. */
export async function stopAndTranscribe(recorder: AudioRecorder): Promise<string> {
  await recorder.stop();
  const uri = recorder.uri;
  if (!uri) throw new Error('Nothing was recorded.');

  const clip = new File(uri);
  const response = await fetch(`${API_BASE}/api/voice/transcribe`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: await clip.bytes(),
  });
  const body = await response.json().catch(() => ({}));
  clip.delete();
  if (!response.ok) throw new Error(body?.detail ?? `Transcription failed (${response.status})`);
  return String(body.text ?? '').trim();
}

// One player at a time. Holding the reference keeps it alive through playback,
// and replacing it stops an older line talking over a newer one.
let speaking: AudioPlayer | null = null;

/**
 * Speak a line back. Failures are swallowed on purpose: losing audio should
 * never cost the user a decision they can already read on screen.
 */
export async function say(text: string): Promise<void> {
  try {
    const response = await fetch(`${API_BASE}/api/voice/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!response.ok) return;

    const mp3 = new File(Paths.cache, 'bounded-mandate-speech.mp3');
    mp3.create({ overwrite: true });
    mp3.write(new Uint8Array(await response.arrayBuffer()));

    speaking?.remove();
    speaking = createAudioPlayer(mp3.uri);
    speaking.play();
  } catch {
    /* silent — the screen already said it */
  }
}
