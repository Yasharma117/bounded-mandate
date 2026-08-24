import { useAudioRecorder } from 'expo-audio';
import { GlassView, isLiquidGlassAvailable } from 'expo-glass-effect';
import { Image } from 'expo-image';
import { useCallback, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
  type ScrollView as ScrollViewType,
} from 'react-native';
import Animated, { FadeIn } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Bubble } from '@/components/bubble';
import { DecisionCard } from '@/components/decision-card';
import { runAgent, rupees, type Decision } from '@/lib/api';
import { RECORDING_OPTIONS, say, startListening, stopAndTranscribe } from '@/lib/voice';
import { colors } from '@/theme/colors';

type Message =
  | { id: string; kind: 'user' | 'agent'; text: string }
  | { id: string; kind: 'decision'; decision: Decision };

/**
 * Openers, not scenarios. Each is a thing a person would actually say; the last
 * one hands the account to an agent that is working against them, which is the
 * only claim in this app worth testing out loud.
 */
const OPENERS: { label: string; text: string; adversarial?: boolean }[] = [
  { label: 'Milk, eggs and bread', text: 'Order just milk, eggs and brown bread from Instamart.' },
  { label: 'My usual groceries', text: 'Order my usual groceries from Instamart.' },
  {
    label: 'Add earbuds and a case',
    text: 'Order my usual groceries, and add the Bluetooth earbuds and a phone case.',
  },
  { label: 'Run a compromised agent', text: 'Order my usual groceries.', adversarial: true },
];

/** What the agent says once the engine has ruled. The verdict leads. */
function narrate(decision: Decision): string {
  switch (decision.verdict) {
    case 'ALLOW':
      return `Ordered your groceries — ${rupees(decision.real_total_paise)}, within your rule.`;
    case 'ESCALATE':
      return `This one needs you — ${rupees(decision.real_total_paise)}. Here's why:`;
    case 'CLARIFY':
      return `I'm not sure this is in scope. Before I spend anything:`;
    case 'DENY':
      return `I couldn't complete that order, and nothing was charged.`;
  }
}

export default function Thread() {
  const insets = useSafeAreaInsets();
  const scroller = useRef<ScrollViewType>(null);
  const [busy, setBusy] = useState(false);
  const [messages, setMessages] = useState<Message[]>([
    {
      id: 'rule',
      kind: 'user',
      text: 'Order my usual groceries from Instamart every 4 days, keep each under ₹2,000',
    },
    {
      id: 'ack',
      kind: 'agent',
      text: "Done. I'll place each order myself and only interrupt you when something crosses one of those lines.",
    },
  ]);

  const add = useCallback((...added: Message[]) => {
    setMessages((prior) => [...prior, ...added]);
    requestAnimationFrame(() => scroller.current?.scrollToEnd({ animated: true }));
  }, []);

  const send = useCallback(
    async (text: string, adversarial = false) => {
      const said = text.trim();
      if (!said || busy) return;
      setBusy(true);
      const turn = Date.now();
      add({ id: `u-${turn}`, kind: 'user', text: said });
      try {
        const result = await runAgent(said, adversarial);
        const spoken = result.decision ? narrate(result.decision) : result.said;
        add(
          { id: `a-${turn}`, kind: 'agent', text: spoken },
          ...(result.decision
            ? [{ id: `d-${turn}`, kind: 'decision' as const, decision: result.decision }]
            : []),
        );
        void say(spoken);
      } catch (error) {
        add({ id: `e-${turn}`, kind: 'agent', text: (error as Error).message });
      } finally {
        setBusy(false);
      }
    },
    [add, busy],
  );

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <ScrollView
        ref={scroller}
        contentInsetAdjustmentBehavior="automatic"
        keyboardDismissMode="interactive"
        contentContainerStyle={{ padding: 16, gap: 14, paddingBottom: insets.bottom + 190 }}
      >
        {messages.map((message) =>
          message.kind === 'decision' ? (
            <DecisionCard key={message.id} decision={message.decision} />
          ) : (
            <Bubble key={message.id} from={message.kind}>
              {message.text}
            </Bubble>
          ),
        )}
        {busy && <ActivityIndicator style={{ alignSelf: 'flex-start' }} />}
      </ScrollView>

      <Composer busy={busy} onSend={send} bottomInset={insets.bottom} />
    </View>
  );
}

/** Pinned to the bottom. Speaking and typing are the same input. */
function Composer({
  busy,
  onSend,
  bottomInset,
}: {
  busy: boolean;
  onSend: (text: string, adversarial?: boolean) => void;
  bottomInset: number;
}) {
  const recorder = useAudioRecorder(RECORDING_OPTIONS);
  const [draft, setDraft] = useState('');
  const [listening, setListening] = useState(false);
  const [micError, setMicError] = useState('');
  const Surface = isLiquidGlassAvailable() ? GlassView : View;

  const toggleMic = useCallback(async () => {
    setMicError('');
    if (!listening) {
      try {
        await startListening(recorder);
        setListening(true);
      } catch (error) {
        setMicError((error as Error).message);
      }
      return;
    }
    setListening(false);
    try {
      const heard = await stopAndTranscribe(recorder);
      // Straight through: speaking is how this app is meant to be used, and a
      // confirm step before every sentence is the friction it exists to remove.
      if (heard) onSend(heard);
      else setMicError("I didn't catch that.");
    } catch (error) {
      setMicError((error as Error).message);
    }
  }, [listening, onSend, recorder]);

  const submit = () => {
    onSend(draft);
    setDraft('');
  };

  return (
    <Animated.View
      entering={FadeIn}
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        bottom: 0,
        paddingHorizontal: 16,
        paddingBottom: bottomInset + 10,
        gap: 10,
      }}
    >
      {micError ? (
        <Text selectable style={{ fontSize: 12, color: colors.red, paddingHorizontal: 4 }}>
          {micError}
        </Text>
      ) : null}

      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
        contentContainerStyle={{ gap: 8, paddingVertical: 2 }}
      >
        {OPENERS.map((opener) => (
          <Pressable
            key={opener.label}
            disabled={busy}
            onPress={() => onSend(opener.text, opener.adversarial)}
            style={({ pressed }) => ({
              backgroundColor: colors.fill,
              paddingHorizontal: 14,
              paddingVertical: 9,
              borderRadius: 18,
              borderCurve: 'continuous',
              opacity: busy ? 0.4 : pressed ? 0.7 : 1,
            })}
          >
            <Text style={{ fontSize: 13, color: colors.label }}>{opener.label}</Text>
          </Pressable>
        ))}
      </ScrollView>

      <Surface
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          gap: 10,
          paddingHorizontal: 16,
          paddingVertical: 8,
          borderRadius: 26,
          borderCurve: 'continuous',
          backgroundColor: isLiquidGlassAvailable() ? undefined : colors.grouped,
        }}
      >
        <TextInput
          value={draft}
          onChangeText={setDraft}
          onSubmitEditing={submit}
          editable={!busy && !listening}
          returnKeyType="send"
          placeholder={listening ? 'Listening…' : 'Ask Bounded Mandate anything…'}
          placeholderTextColor={colors.tertiaryLabel}
          style={{ flex: 1, fontSize: 16, color: colors.label, paddingVertical: 6 }}
        />
        {draft.trim() ? (
          <Pressable onPress={submit} disabled={busy} hitSlop={10}>
            <Image
              source="sf:arrow.up.circle.fill"
              tintColor={colors.blue as string}
              style={{ width: 26, height: 26 }}
            />
          </Pressable>
        ) : (
          <Pressable onPress={toggleMic} disabled={busy} hitSlop={10}>
            <Image
              source={listening ? 'sf:stop.circle.fill' : 'sf:mic.fill'}
              tintColor={(listening ? colors.red : colors.secondaryLabel) as string}
              style={{ width: listening ? 26 : 19, height: listening ? 26 : 19 }}
            />
          </Pressable>
        )}
      </Surface>
    </Animated.View>
  );
}
