// Shared Tailwind class patterns for Sources components

export const styles = {
  // Layout
  panel: 'bg-white p-6 rounded-xl shadow-sm',
  panelHeader: 'flex items-center justify-between mb-4',
  panelTitle: 'text-lg font-semibold text-slate-800',
  sourceList: 'space-y-3',

  // Buttons
  btnPrimary: 'py-2 px-4 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary-dark disabled:bg-slate-300 disabled:cursor-not-allowed',
  btnSecondary: 'py-2 px-4 bg-slate-100 text-slate-700 rounded-lg text-sm hover:bg-slate-200',
  btnDanger: 'py-2 px-4 bg-red-500 text-white rounded-lg text-sm hover:bg-red-600',
  btnSmall: 'py-1 px-2 text-xs rounded',
  btnAdd: 'py-2 px-4 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary-dark',
  btnEdit: 'py-1 px-3 bg-slate-100 text-slate-600 rounded text-sm hover:bg-slate-200',
  btnDelete: 'py-1 px-3 bg-red-50 text-red-600 rounded text-sm hover:bg-red-100',
  btnCancel: 'py-2 px-4 border border-slate-200 rounded-lg text-slate-600 hover:bg-slate-50',
  btnSubmit: 'py-2 px-4 bg-primary text-white rounded-lg font-medium hover:bg-primary-dark disabled:bg-slate-300 disabled:cursor-not-allowed',
  btnUpload: 'py-2 px-4 bg-slate-100 text-slate-700 rounded-lg text-sm cursor-pointer hover:bg-slate-200',

  // Cards
  card: 'border border-slate-200 rounded-lg p-4 hover:border-slate-300 transition-colors',
  cardInactive: 'border border-slate-200 rounded-lg p-4 opacity-60',
  cardHeader: 'flex items-start justify-between',
  cardInfo: 'flex-1 min-w-0',
  cardTitle: 'font-medium text-slate-800 truncate',
  cardSubtitle: 'text-sm text-slate-500 truncate',
  cardActions: 'flex items-center gap-2 ml-4 flex-shrink-0',

  // Forms
  form: 'space-y-4',
  formGroup: 'space-y-1',
  formLabel: 'block text-sm font-medium text-slate-700',
  formInput: 'w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20',
  formSelect: 'w-full py-2 px-3 border border-slate-200 rounded-lg text-sm bg-white focus:border-primary focus:outline-none',
  formTextarea: 'w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 resize-none',
  formHint: 'text-xs text-slate-500',
  formError: 'p-3 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm',
  formRow: 'grid grid-cols-2 gap-4',
  formActions: 'flex justify-end gap-3 pt-4',
  formCheckbox: 'flex items-center gap-2',

  // Modals
  modalOverlay: 'fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4',
  modalContent: 'bg-white rounded-xl shadow-xl max-w-lg w-full p-6 max-h-[90vh] overflow-y-auto',
  modalTitle: 'text-lg font-semibold text-slate-800 mb-2',
  modalDescription: 'text-slate-600 mb-4',
  modalActions: 'flex justify-end gap-3 pt-4',

  // Status & badges
  errorBanner: 'bg-red-50 text-red-700 py-2 px-3 rounded text-sm my-2',
  successBanner: 'bg-green-50 text-green-700 py-2 px-3 rounded text-sm my-2',
  badge: 'px-2 py-0.5 rounded text-xs font-medium',
  badgeActive: 'bg-green-100 text-green-700',
  badgeInactive: 'bg-slate-100 text-slate-500',

  // Misc
  emptyState: 'text-center py-8 text-slate-500',
  configBox: 'bg-slate-50 border border-slate-200 rounded-lg p-4',
  detailsSection: 'border border-slate-200 rounded-lg',
  detailsSummary: 'p-3 cursor-pointer hover:bg-slate-50 font-medium text-slate-700',
  detailsContent: 'p-4 pt-0',
} as const

// Helper to combine classes
export const cx = (...classes: (string | undefined | false)[]) =>
  classes.filter(Boolean).join(' ')
