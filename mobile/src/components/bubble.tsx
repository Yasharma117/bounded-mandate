import { Text, View } from 'react-native';
import Animated, { FadeInDown } from 'react-native-reanimated';

import { colors } from '@/theme/colors';

/**
 * The spine is a conversation. A user turn is a tinted capsule on the right;
 * the agent speaks plainly on the left, wider, because it is the one
 * explaining itself.
 */
export function Bubble({ from, children }: { from: 'user' | 'agent'; children: string }) {
  const isUser = from === 'user';
  return (
    <Animated.View
      entering={FadeInDown.springify().damping(18)}
      style={{ alignSelf: isUser ? 'flex-end' : 'flex-start', maxWidth: '86%' }}
    >
      <View
        style={{
          backgroundColor: isUser ? colors.fill : 'transparent',
          paddingHorizontal: isUser ? 14 : 2,
          paddingVertical: isUser ? 10 : 2,
          borderRadius: 20,
          borderCurve: 'continuous',
        }}
      >
        <Text selectable style={{ fontSize: 16, lineHeight: 22, color: colors.label }}>
          {children}
        </Text>
      </View>
    </Animated.View>
  );
}
