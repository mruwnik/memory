import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useProjects, ProjectsOverview, Milestone, RepoMilestones } from '@/hooks/useProjects'

interface MilestoneCardProps {
  milestone: Milestone
  onSelect: (milestone: Milestone) => void
}

const MilestoneCard = ({ milestone, onSelect }: MilestoneCardProps) => {
  const isOverdue = milestone.due_on && new Date(milestone.due_on) < new Date() && milestone.state === 'open'
  const dueSoon = milestone.due_on && !isOverdue && milestone.state === 'open' &&
    new Date(milestone.due_on) < new Date(Date.now() + 7 * 24 * 60 * 60 * 1000)

  return (
    <div
      className={`bg-white border rounded-lg p-4 hover:shadow-md transition-shadow cursor-pointer ${
        isOverdue ? 'border-red-300' : dueSoon ? 'border-yellow-300' : 'border-slate-200'
      }`}
      onClick={() => onSelect(milestone)}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex-1 min-w-0">
          <h3 className="font-medium text-slate-800 truncate">{milestone.title}</h3>
          <p className="text-sm text-slate-500 truncate">{milestone.repo_path}</p>
        </div>
        <span className={`ml-2 px-2 py-1 text-xs rounded-full ${
          milestone.state === 'open'
            ? 'bg-green-100 text-green-700'
            : 'bg-slate-100 text-slate-600'
        }`}>
          {milestone.state}
        </span>
      </div>

      {/* Progress bar */}
      <div className="mb-3">
        <div className="flex justify-between text-sm mb-1">
          <span className="text-slate-600">{milestone.progress_percent}% complete</span>
          <span className="text-slate-500">
            {milestone.closed_issues}/{milestone.total_issues} issues
          </span>
        </div>
        <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
          <div
            className={`h-full transition-all ${
              milestone.progress_percent === 100
                ? 'bg-green-500'
                : milestone.progress_percent > 50
                  ? 'bg-primary'
                  : 'bg-yellow-500'
            }`}
            style={{ width: `${milestone.progress_percent}%` }}
          />
        </div>
      </div>

      {/* Due date */}
      {milestone.due_on && (
        <div className={`text-sm ${
          isOverdue ? 'text-red-600 font-medium' : dueSoon ? 'text-yellow-600' : 'text-slate-500'
        }`}>
          {isOverdue ? 'Overdue: ' : 'Due: '}
          {new Date(milestone.due_on).toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric',
            year: 'numeric'
          })}
        </div>
      )}
    </div>
  )
}

interface RepoSectionProps {
  repo: RepoMilestones
  onSelectMilestone: (milestone: Milestone) => void
  defaultExpanded?: boolean
}

const RepoSection = ({ repo, onSelectMilestone, defaultExpanded = true }: RepoSectionProps) => {
  const [expanded, setExpanded] = useState(defaultExpanded)

  // Extract client name from repo name (e.g., "METR_Issues" -> "METR")
  const clientName = repo.repo_name.replace(/_Issues$/, '').replace(/_/g, ' ')

  return (
    <div className="mb-6">
      <button
        className="w-full flex items-center justify-between p-4 bg-slate-100 rounded-lg hover:bg-slate-200 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-3">
          <span className="text-lg font-semibold text-slate-800">{clientName}</span>
          <span className="text-sm text-slate-500">
            {repo.total_open_milestones} open, {repo.total_closed_milestones} closed
          </span>
        </div>
        <span className="text-slate-400 text-xl">{expanded ? '-' : '+'}</span>
      </button>

      {expanded && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mt-4">
          {repo.milestones.map((milestone) => (
            <MilestoneCard
              key={milestone.id}
              milestone={milestone}
              onSelect={onSelectMilestone}
            />
          ))}
        </div>
      )}
    </div>
  )
}

const Projects = () => {
  const { getMilestones, loading, error } = useProjects()
  const [data, setData] = useState<ProjectsOverview | null>(null)
  const [selectedMilestone, setSelectedMilestone] = useState<Milestone | null>(null)
  const [showClosed, setShowClosed] = useState(false)

  const loadData = useCallback(async () => {
    const result = await getMilestones({ includeClosed: showClosed })
    setData(result)
  }, [getMilestones, showClosed])

  useEffect(() => {
    loadData()
  }, [loadData])

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <header className="flex items-center gap-4 mb-6 pb-4 border-b border-slate-200">
        <Link
          to="/ui/dashboard"
          className="bg-slate-50 text-slate-600 border border-slate-200 py-2 px-4 rounded-md text-sm hover:bg-slate-100"
        >
          Back
        </Link>
        <h1 className="text-2xl font-semibold text-slate-800 flex-1">Projects</h1>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={showClosed}
              onChange={(e) => setShowClosed(e.target.checked)}
              className="rounded border-slate-300"
            />
            Show closed
          </label>
          <button
            onClick={loadData}
            className="px-4 py-2 bg-primary text-white rounded-md hover:bg-primary-dark text-sm"
          >
            Refresh
          </button>
        </div>
      </header>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg mb-4 flex justify-between items-center">
          <p>{error}</p>
          <button onClick={loadData} className="text-primary hover:underline">
            Retry
          </button>
        </div>
      )}

      {/* Summary stats */}
      {data && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <div className="bg-white rounded-lg p-4 border border-slate-200">
            <div className="text-3xl font-bold text-slate-800">{data.total_repos}</div>
            <div className="text-sm text-slate-500">Repositories</div>
          </div>
          <div className="bg-white rounded-lg p-4 border border-slate-200">
            <div className="text-3xl font-bold text-green-600">{data.total_open_milestones}</div>
            <div className="text-sm text-slate-500">Open Milestones</div>
          </div>
          <div className="bg-white rounded-lg p-4 border border-slate-200">
            <div className="text-3xl font-bold text-slate-600">{data.total_closed_milestones}</div>
            <div className="text-sm text-slate-500">Closed Milestones</div>
          </div>
          <div className="bg-white rounded-lg p-4 border border-slate-200">
            <div className="text-sm font-medium text-slate-800">
              {data.last_updated
                ? new Date(data.last_updated).toLocaleString('en-US', {
                    month: 'short',
                    day: 'numeric',
                    hour: 'numeric',
                    minute: '2-digit'
                  })
                : 'Never'}
            </div>
            <div className="text-sm text-slate-500">Last Updated</div>
          </div>
        </div>
      )}

      {/* Milestones grouped by repo */}
      {data && data.repos.length > 0 ? (
        <div>
          {data.repos.map((repo) => (
            <RepoSection
              key={repo.repo_path}
              repo={repo}
              onSelectMilestone={setSelectedMilestone}
            />
          ))}
        </div>
      ) : !loading ? (
        <div className="text-center py-12">
          <div className="text-slate-400 text-lg mb-2">No milestones found</div>
          <p className="text-slate-500 text-sm">
            Configure GitHub repos to track in the Sources page
          </p>
          <Link
            to="/ui/sources"
            className="inline-block mt-4 text-primary hover:underline"
          >
            Go to Sources
          </Link>
        </div>
      ) : null}

      {loading && (
        <div className="fixed inset-0 bg-white/80 flex items-center justify-center z-10">
          <div className="text-slate-600">Loading projects...</div>
        </div>
      )}

      {/* Milestone Detail Modal */}
      {selectedMilestone && (
        <div
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
          onClick={() => setSelectedMilestone(null)}
        >
          <div
            className="bg-white rounded-xl shadow-xl max-w-lg w-full max-h-[80vh] overflow-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between p-6 border-b border-slate-100">
              <div>
                <h2 className="text-xl font-semibold text-slate-800">
                  {selectedMilestone.title}
                </h2>
                <p className="text-sm text-slate-500 mt-1">{selectedMilestone.repo_path}</p>
              </div>
              <button
                className="text-slate-400 hover:text-slate-600 text-2xl leading-none"
                onClick={() => setSelectedMilestone(null)}
              >
                &times;
              </button>
            </div>
            <div className="p-6 space-y-4">
              {/* Progress */}
              <div>
                <div className="flex justify-between text-sm mb-2">
                  <span className="font-medium text-slate-700">Progress</span>
                  <span className="text-slate-600">{selectedMilestone.progress_percent}%</span>
                </div>
                <div className="h-3 bg-slate-100 rounded-full overflow-hidden">
                  <div
                    className={`h-full transition-all ${
                      selectedMilestone.progress_percent === 100
                        ? 'bg-green-500'
                        : 'bg-primary'
                    }`}
                    style={{ width: `${selectedMilestone.progress_percent}%` }}
                  />
                </div>
              </div>

              {/* Issue counts */}
              <div className="grid grid-cols-3 gap-4 text-center">
                <div className="bg-slate-50 rounded-lg p-3">
                  <div className="text-2xl font-bold text-slate-800">
                    {selectedMilestone.total_issues}
                  </div>
                  <div className="text-xs text-slate-500">Total</div>
                </div>
                <div className="bg-green-50 rounded-lg p-3">
                  <div className="text-2xl font-bold text-green-600">
                    {selectedMilestone.closed_issues}
                  </div>
                  <div className="text-xs text-slate-500">Closed</div>
                </div>
                <div className="bg-yellow-50 rounded-lg p-3">
                  <div className="text-2xl font-bold text-yellow-600">
                    {selectedMilestone.open_issues}
                  </div>
                  <div className="text-xs text-slate-500">Open</div>
                </div>
              </div>

              {/* Details */}
              <div className="space-y-3 pt-2">
                <div className="flex">
                  <span className="w-24 text-sm text-slate-500 shrink-0">Status</span>
                  <span className={`px-2 py-1 text-xs rounded-full ${
                    selectedMilestone.state === 'open'
                      ? 'bg-green-100 text-green-700'
                      : 'bg-slate-100 text-slate-600'
                  }`}>
                    {selectedMilestone.state}
                  </span>
                </div>

                {selectedMilestone.due_on && (
                  <div className="flex">
                    <span className="w-24 text-sm text-slate-500 shrink-0">Due Date</span>
                    <span className="text-sm text-slate-800">
                      {new Date(selectedMilestone.due_on).toLocaleDateString('en-US', {
                        weekday: 'long',
                        year: 'numeric',
                        month: 'long',
                        day: 'numeric'
                      })}
                    </span>
                  </div>
                )}

                {selectedMilestone.description && (
                  <div>
                    <span className="text-sm text-slate-500 block mb-1">Description</span>
                    <p className="text-sm text-slate-700 bg-slate-50 p-3 rounded-lg">
                      {selectedMilestone.description}
                    </p>
                  </div>
                )}
              </div>

              {/* Actions */}
              <div className="pt-4 border-t border-slate-100">
                <a
                  href={selectedMilestone.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-md hover:bg-primary-dark text-sm"
                >
                  View on GitHub
                  <span className="text-xs">↗</span>
                </a>
              </div>
            </div>
          </div>
        </div>
      )}

      <footer className="mt-6 text-center">
        <Link to="/ui/sources" className="text-primary text-sm hover:underline">
          Configure GitHub repositories
        </Link>
      </footer>
    </div>
  )
}

export default Projects
