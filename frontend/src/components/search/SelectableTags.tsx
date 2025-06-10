interface SelectableTagProps {
    tag: string
    selected: boolean
    onSelect: (tag: string, selected: boolean) => void
}

const SelectableTag = ({ tag, selected, onSelect }: SelectableTagProps) => {
    return (
        <span 
            className={`tag ${selected ? 'selected' : ''}`} 
            onClick={() => onSelect(tag, !selected)}
        >
            {tag}
        </span>
    )
}

import { useState } from 'react'

interface SelectableTagsProps {
    title: string
    className: string
    tags: Record<string, boolean>
    onSelect: (tag: string, selected: boolean) => void
    onBatchUpdate?: (updates: Record<string, boolean>) => void
    searchable?: boolean
}

export const SelectableTags = ({ title, className, tags, onSelect, onBatchUpdate, searchable = false }: SelectableTagsProps) => {
    const [searchTerm, setSearchTerm] = useState('')
    const handleSelectAll = () => {
        if (onBatchUpdate) {
            const updates = Object.keys(tags).reduce((acc, tag) => {
                acc[tag] = true
                return acc
            }, {} as Record<string, boolean>)
            onBatchUpdate(updates)
        } else {
            // Fallback to individual updates (though this won't work well)
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
            // Fallback to individual updates (though this won't work well)
            Object.keys(tags).forEach(tag => {
                if (tags[tag]) {
                    onSelect(tag, false)
                }
            })
        }
    }

    // Filter tags based on search term
    const filteredTags = Object.entries(tags).filter(([tag, selected]) => {
        return !searchTerm || tag.toLowerCase().includes(searchTerm.toLowerCase())
    })

    const selectedCount = Object.values(tags).filter(Boolean).length
    const totalCount = Object.keys(tags).length
    const filteredSelectedCount = filteredTags.filter(([_, selected]) => selected).length
    const filteredTotalCount = filteredTags.length
    const allSelected = selectedCount === totalCount
    const noneSelected = selectedCount === 0

    return (
        <div className="search-option">
            <details className="selectable-tags-details">
                <summary className="selectable-tags-summary">
                    {title} ({selectedCount} selected)
                </summary>
                
                <div className="tag-controls">
                    <button 
                        type="button"
                        className="tag-control-btn"
                        onClick={handleSelectAll}
                        disabled={allSelected}
                    >
                        All
                    </button>
                    <button 
                        type="button"
                        className="tag-control-btn"
                        onClick={handleDeselectAll}
                        disabled={noneSelected}
                    >
                        None
                    </button>
                    <span className="tag-count">
                        ({selectedCount}/{totalCount})
                    </span>
                </div>
                
                {searchable && (
                    <div className="tag-search-controls">
                        <input
                            type="text"
                            placeholder={`Search ${title.toLowerCase()}...`}
                            value={searchTerm}
                            onChange={(e) => setSearchTerm(e.target.value)}
                            className="tag-search-input"
                        />
                        {searchTerm && (
                            <span className="filtered-count">
                                Showing {filteredSelectedCount}/{filteredTotalCount}
                            </span>
                        )}
                    </div>
                )}
                
                <div className={`${className} tags-display-area`}>
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
        </div>
    )
} 