import { useState } from 'react'

interface SelectableTagProps {
    tag: string
    selected: boolean
    onSelect: (tag: string, selected: boolean) => void
}

const SelectableTag = ({ tag, selected, onSelect }: SelectableTagProps) => {
    return (
        <span
            className={`px-2 py-1 rounded text-xs cursor-pointer transition-colors ${
                selected
                    ? 'bg-primary text-white'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
            }`}
            onClick={() => onSelect(tag, !selected)}
        >
            {tag}
        </span>
    )
}

interface SelectableTagsProps {
    title: string
    tags: Record<string, boolean>
    onSelect: (tag: string, selected: boolean) => void
    onBatchUpdate?: (updates: Record<string, boolean>) => void
    searchable?: boolean
}

export const SelectableTags = ({ title, tags, onSelect, onBatchUpdate, searchable = false }: SelectableTagsProps) => {
    const [searchTerm, setSearchTerm] = useState('')

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

    const filteredTags = Object.entries(tags).filter(([tag]) => {
        return !searchTerm || tag.toLowerCase().includes(searchTerm.toLowerCase())
    })

    const selectedCount = Object.values(tags).filter(Boolean).length
    const totalCount = Object.keys(tags).length
    const filteredSelectedCount = filteredTags.filter(([_, selected]) => selected).length
    const filteredTotalCount = filteredTags.length
    const allSelected = selectedCount === totalCount
    const noneSelected = selectedCount === 0

    return (
        <details className="border border-slate-200 rounded-lg p-4">
            <summary className="cursor-pointer font-medium text-slate-700">
                {title} ({selectedCount} selected)
            </summary>

            <div className="mt-4 flex items-center gap-2">
                <button
                    type="button"
                    className="px-3 py-1 text-xs bg-slate-100 text-slate-600 rounded hover:bg-slate-200 disabled:opacity-50 disabled:cursor-not-allowed"
                    onClick={handleSelectAll}
                    disabled={allSelected}
                >
                    All
                </button>
                <button
                    type="button"
                    className="px-3 py-1 text-xs bg-slate-100 text-slate-600 rounded hover:bg-slate-200 disabled:opacity-50 disabled:cursor-not-allowed"
                    onClick={handleDeselectAll}
                    disabled={noneSelected}
                >
                    None
                </button>
                <span className="text-xs text-slate-500">
                    ({selectedCount}/{totalCount})
                </span>
            </div>

            {searchable && (
                <div className="mt-3 flex items-center gap-2">
                    <input
                        type="text"
                        placeholder={`Search ${title.toLowerCase()}...`}
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                        className="flex-1 py-1 px-2 border border-slate-200 rounded text-sm"
                    />
                    {searchTerm && (
                        <span className="text-xs text-slate-500">
                            Showing {filteredSelectedCount}/{filteredTotalCount}
                        </span>
                    )}
                </div>
            )}

            <div className="mt-3 flex flex-wrap gap-1.5 max-h-48 overflow-y-auto">
                {filteredTags.map(([tag, selected]: [string, boolean]) => (
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
