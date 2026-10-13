// Categorical palette (dark steps of the validated reference palette, checked against the app surface #12151c).
// Hues are assigned in fixed order and follow the entity, never its rank.
export const SERIES = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'];

export const SERIES_FOR = { conversations: SERIES[0], escalated: SERIES[1], resolved: SERIES[2] };

export const axis = { stroke: '#2a3040', tick: { fill: '#8b93a7', fontSize: 11 }, tickLine: false };
export const grid = { stroke: '#232838', strokeDasharray: '0', vertical: false };
export const tooltip = {
  contentStyle: { background: '#161a23', border: '1px solid #2a3040', borderRadius: 8, color: '#e6e8ee', fontSize: 12 },
  labelStyle: { color: '#8b93a7', marginBottom: 4 },
  itemStyle: { color: '#e6e8ee', padding: 0 },
  cursor: { stroke: '#3a4255', strokeWidth: 1 },
};
export const barCursor = { fill: 'rgba(255,255,255,0.04)' };
