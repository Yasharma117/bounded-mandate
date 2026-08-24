import { Image } from 'expo-image';
import { Text, View } from 'react-native';
import Animated, { FadeInDown } from 'react-native-reanimated';

import { colors, verdictColor } from '@/theme/colors';
import { rupees, type Decision } from '@/lib/api';

const SYMBOL: Record<Decision['verdict'], string> = {
  ALLOW: 'sf:checkmark.seal.fill',
  CLARIFY: 'sf:questionmark.circle.fill',
  ESCALATE: 'sf:exclamationmark.triangle.fill',
  DENY: 'sf:hand.raised.fill',
};

const HEADLINE: Record<Decision['verdict'], string> = {
  ALLOW: 'Paid · logged',
  CLARIFY: 'Needs an answer',
  ESCALATE: 'Your call',
  DENY: 'Refused',
};

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 12 }}>
      <Text style={{ fontSize: 13, color: colors.secondaryLabel }}>{label}</Text>
      <Text
        selectable
        style={{
          fontSize: 13,
          color: colors.label,
          fontVariant: ['tabular-nums'],
          flexShrink: 1,
          textAlign: 'right',
        }}
      >
        {value}
      </Text>
    </View>
  );
}

/**
 * Money renders as a card inside the thread, never as a separate screen — the
 * card is punctuation at the end of a turn.
 *
 * Every verdict uses this one component. A receipt and a refusal are the same
 * object wearing different colours, which is the honest shape: the engine ran
 * the same checks either way, and the user should be able to read both the
 * same way.
 */
export function DecisionCard({ decision }: { decision: Decision }) {
  const tint = verdictColor[decision.verdict];
  const lied = decision.claimed_total_paise !== decision.real_total_paise;

  return (
    <Animated.View
      entering={FadeInDown.springify().damping(18)}
      style={{
        backgroundColor: colors.grouped,
        borderRadius: 18,
        borderCurve: 'continuous',
        overflow: 'hidden',
        boxShadow: '0 1px 2px rgba(0,0,0,0.06)',
      }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, padding: 14 }}>
        <Image source={SYMBOL[decision.verdict]} tintColor={tint as string} style={{ width: 15, height: 15 }} />
        <Text style={{ fontSize: 12, fontWeight: '600', letterSpacing: 0.4, color: tint }}>
          {HEADLINE[decision.verdict].toUpperCase()}
        </Text>
      </View>

      <View style={{ paddingHorizontal: 14, paddingBottom: 12, gap: 2 }}>
        <Text
          selectable
          style={{
            fontSize: 30,
            fontWeight: '600',
            color: colors.label,
            fontVariant: ['tabular-nums'],
          }}
        >
          {rupees(decision.real_total_paise)}
        </Text>
        {lied ? (
          <Text style={{ fontSize: 13, color: verdictColor.DENY }}>
            the agent reported {rupees(decision.claimed_total_paise)}
          </Text>
        ) : (
          <Text style={{ fontSize: 13, color: colors.secondaryLabel }}>Instamart</Text>
        )}
      </View>

      {decision.reasons.length > 0 && (
        <View style={{ gap: 8, paddingHorizontal: 14, paddingBottom: 12 }}>
          {decision.reasons.map((reason) => (
            <View
              key={reason.code}
              style={{
                borderLeftWidth: 2,
                borderLeftColor: tint,
                paddingLeft: 10,
                gap: 2,
              }}
            >
              <Text style={{ fontSize: 11, color: colors.tertiaryLabel, fontFamily: 'Menlo' }}>
                {reason.code}
              </Text>
              <Text selectable style={{ fontSize: 14, lineHeight: 19, color: colors.label }}>
                {reason.detail}
              </Text>
            </View>
          ))}
        </View>
      )}

      <View style={{ height: 1, backgroundColor: colors.separator, opacity: 0.6 }} />
      <View style={{ padding: 14, gap: 6 }}>
        <Row label="Decision" value={decision.reason_code} />
        {decision.order_id ? (
          <Row label="Razorpay" value={decision.order_id} />
        ) : (
          <Row label="Reached the rail" value="no" />
        )}
      </View>
    </Animated.View>
  );
}
