import { Color } from 'expo-router';
import { Platform } from 'react-native';

/** Native semantic colours — they resolve on-device and follow light/dark. */
export const colors = {
  label: Platform.select({ ios: Color.ios.label, default: '#000000' })!,
  secondaryLabel: Platform.select({ ios: Color.ios.secondaryLabel, default: '#3c3c43' })!,
  tertiaryLabel: Platform.select({ ios: Color.ios.tertiaryLabel, default: '#8a8a8e' })!,
  separator: Platform.select({ ios: Color.ios.separator, default: '#c6c6c8' })!,
  background: Platform.select({ ios: Color.ios.systemBackground, default: '#ffffff' })!,
  grouped: Platform.select({ ios: Color.ios.secondarySystemBackground, default: '#f2f2f7' })!,
  fill: Platform.select({ ios: Color.ios.tertiarySystemFill, default: '#e5e5ea' })!,
  blue: Platform.select({ ios: Color.ios.systemBlue, default: '#007aff' })!,
  green: Platform.select({ ios: Color.ios.systemGreen, default: '#34c759' })!,
  orange: Platform.select({ ios: Color.ios.systemOrange, default: '#ff9500' })!,
  red: Platform.select({ ios: Color.ios.systemRed, default: '#ff3b30' })!,
} as const;

/** One verdict, one colour, everywhere it appears. */
export const verdictColor = {
  ALLOW: colors.green,
  CLARIFY: colors.blue,
  ESCALATE: colors.orange,
  DENY: colors.red,
} as const;
