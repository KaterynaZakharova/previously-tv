/** Optional native adapter. The shipping WebView uses the same glyphs as local SVG. */
import React from 'react';
import { Play, Pause, SkipBack, SkipForward, ArrowLeft, ArrowRight, ArrowUp,
  ArrowDown, Info, GitMerge, MoreHorizontal, X, RotateCcw, Check,
  HelpCircle, ChevronDown, Home, Tv, Film, User, ExternalLink,
  List, RefreshCw, AlertCircle } from 'react-native-feather';

const icons = {
  play: Play, pause: Pause, 'skip-back': SkipBack, 'skip-forward': SkipForward,
  'arrow-left': ArrowLeft, 'arrow-right': ArrowRight, 'arrow-up': ArrowUp,
  'arrow-down': ArrowDown, info: Info, 'git-merge': GitMerge,
  'more-horizontal': MoreHorizontal, x: X, 'rotate-ccw': RotateCcw,
  check: Check, 'help-circle': HelpCircle, 'chevron-down': ChevronDown,
  home: Home, tv: Tv, film: Film, user: User, 'external-link': ExternalLink,
  list: List, 'refresh-cw': RefreshCw, 'alert-circle': AlertCircle,
};
export type TVIconName = keyof typeof icons;
export function TVIcon({ name, size = 32, color = '#f3f7f4' }: {
  name: TVIconName; size?: number; color?: string;
}) {
  const Icon = icons[name];
  return <Icon width={size} height={size} stroke={color} strokeWidth={1.8} accessible={false} />;
}
