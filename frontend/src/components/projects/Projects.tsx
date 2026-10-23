import { ProjectsPanel } from '../sources/panels/ProjectsPanel'
import { Page } from '@/components/shared/ui'

const Projects = () => {
  return (
    <Page
      title="Projects"
      width="wide"
    >
      <ProjectsPanel />
    </Page>
  )
}

export default Projects
