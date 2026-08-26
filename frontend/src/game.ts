const SCOPE_NAMES: Record<string, string> = {
  all: 'All leagues',
  'premier-league': 'Premier League',
  bundesliga: 'Bundesliga',
  'la-liga': 'La Liga',
  'primeira-liga': 'Primeira Liga',
  'ligue-1': 'Ligue 1',
  'serie-a': 'Serie A',
  eredivisie: 'Eredivisie',
}

export function scopeName(scope: string): string {
  return SCOPE_NAMES[scope] ?? scope
    .split('-')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

export function durationName(seconds: number): string {
  return `${seconds} seconds`
}

export function timerText(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return `${minutes}:${String(remainder).padStart(2, '0')}`
}
