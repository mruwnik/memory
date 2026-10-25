import IngestionOverview from './IngestionOverview'
import { Link } from 'react-router-dom'
import { Page, Section } from '@/components/shared/ui'
import { visibleSections } from '@/components/shared/nav'
import { useModules } from '@/hooks/useModule'

const Dashboard = ({ hasScope }: { hasScope: (scope: string) => boolean }) => {
  const sections = visibleSections(hasScope, useModules())
  return (
    <Page title="Memory" description="Your self-hosted knowledge base." width="wide">
      <IngestionOverview />
      {sections.map(section => (
        <Section key={section.title} title={section.title}>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {section.items.map(t => (
              <Link key={t.to} to={t.to}
                className="block bg-white rounded-xl border border-slate-200 shadow-sm p-5 transition hover:border-primary hover:shadow-md">
                <h3 className="font-semibold text-slate-800">{t.title}</h3>
                <p className="text-sm text-slate-600 mt-1">{t.description}</p>
              </Link>
            ))}
          </div>
        </Section>
      ))}
    </Page>
  )
}

export default Dashboard
