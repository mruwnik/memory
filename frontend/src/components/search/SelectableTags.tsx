import { Button } from '@/components/shared/ui'

interface SelectableTagProps {
    tag: string
    selected: boolean
    onSelect: (tag: string, selected: boolean) => void
}

const SelectableTag = ({ tag, selected, onSelect }: SelectableTagProps) => {
    return (
        <button
            type="button"
            aria-pressed={selected}
            className={`px-2 py-1 rounded text-xs cursor-pointer transition-colors ${
                selected
                    ? 'bg-primary text-white'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
            }`}
            onClick={() => onSelect(tag, !selected)}
        >
            {tag}
        </button>
    )
}

interface SelectableTagsProps {
    title: string
    tags: Record<string, boolean>
    onSelect: (tag: string, selected: boolean) => void
    onBatchUpdate?: (updates: Record<string, boolean>) => void
}

export const SelectableTags = ({ title, tags, onSelect, onBatchUpdate }: SelectableTagsProps) => {
    const handleSelectAll = () => {
        if (onBatchUpdate) {
            const updates = Object.keys(tags).reduce((acc, tag) => {
                acc[tag] = true
                return acc
            }, {} as Record<string, boolean>)
            onBatchUpdate(updates)
        } else {
            Object.keys(tags).forEach(tag => {
                if (!tags[tag]) {
                    onSelect(tag, true)
                }
            })
        }
    }

    const handleDeselectAll = () => {
        if (onBatchUpdate) {
            const updates = Object.keys(tags).reduce((acc, tag) => {
                acc[tag] = false
                return acc
            }, {} as Record<string, boolean>)
            onBatchUpdate(updates)
        } else {
            Object.keys(tags).forEach(tag => {
                if (tags[tag]) {
                    onSelect(tag, false)
                }
            })
        }
    }

    const selectedCount = Object.values(tags).filter(Boolean).length
    const totalCount = Object.keys(tags).length
    const allSelected = selectedCount === totalCount
    const noneSelected = selectedCount === 0

    return (
        <details className="border border-slate-200 rounded-lg p-4">
            <summary className="cursor-pointer font-medium text-slate-700">
                {title} ({selectedCount} selected)
            </summary>

            <div className="mt-4 flex items-center gap-2">
                <Button size="sm"
                    type="button"
                    onClick={handleSelectAll}
                    disabled={allSelected}
                >
                    All
                </Button>
                <Button size="sm"
                    type="button"
                    onClick={handleDeselectAll}
                    disabled={noneSelected}
                >
                    None
                </Button>
                <span className="text-xs text-slate-500">
                    ({selectedCount}/{totalCount})
                </span>
            </div>

            <div className="mt-3 flex flex-wrap gap-1.5 max-h-48 overflow-y-auto">
                {Object.entries(tags).map(([tag, selected]) => (
                    <SelectableTag
                        key={tag}
                        tag={tag}
                        selected={selected}
                        onSelect={onSelect}
                    />
                ))}
            </div>
        </details>
    )
}
