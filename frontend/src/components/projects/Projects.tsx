import { Link } from 'react-router-dom'
import { ProjectsPanel } from '../sources/panels/ProjectsPanel'

const Projects = () => {
  return (
    <div className="min-h-screen bg-slate-50 p-4 md:p-8">
      <div className="flex items-center gap-4 mb-6">
        <Link
          to="/ui/dashboard"
          className="py-2 px-4 bg-slate-100 text-slate-700 rounded-lg text-sm hover:bg-slate-200"
        >
          Back
        </Link>
        <h2 className="text-2xl font-semibold text-slate-800">Projects</h2>
      </div>

      <ProjectsPanel />
    </div>
  )
}

export default Projects
