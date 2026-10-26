import { Disclosure, NewTabLink } from '@/components/shared/ui'

export const GoogleSetupInstructions = () => {
  const redirectUri = `${window.location.origin}/google-drive/callback`

  return (
    <Disclosure summary="Instructions">
      <ol className="list-decimal pl-5 space-y-3">
        <li>
          <strong>Select a Google Cloud project.</strong> Open the{' '}
          <NewTabLink href="https://console.cloud.google.com/projectselector2/home/dashboard">Google Cloud Console</NewTabLink>.
          Use the project selector at the top to select an existing project or choose New project, give it a name, and create it.
          Keep this same project selected throughout these steps. You can reuse a project for multiple Memory instances;
          create a separate OAuth client for each instance.
        </li>
        <li>
          <strong>Enable the APIs you want to use.</strong> Open each relevant page and click Enable:{' '}
          <NewTabLink href="https://console.cloud.google.com/apis/library/gmail.googleapis.com">Gmail API</NewTabLink>,{' '}
          <NewTabLink href="https://console.cloud.google.com/apis/library/calendar-json.googleapis.com">Google Calendar API</NewTabLink>, and{' '}
          <NewTabLink href="https://console.cloud.google.com/apis/library/drive.googleapis.com">Google Drive API</NewTabLink>.
          If the page shows Manage, it is already enabled. Signing in with Google does not enable these APIs.
        </li>
        <li>
          <strong>Configure the consent screen.</strong> Open{' '}
          <NewTabLink href="https://console.cloud.google.com/auth/branding">Google Auth Platform → Branding</NewTabLink>.
          If prompted, click Get started. Enter an app name such as Memory, your support email, and developer contact email.
          For Audience, choose Internal only if all users belong to your Google Workspace organization; otherwise choose External.
          Complete and save the setup.
        </li>
        <li>
          <strong>Add your test users.</strong> For an External app in Testing mode, open{' '}
          <NewTabLink href="https://console.cloud.google.com/auth/audience">Audience</NewTabLink> → Test users → Add users
          and add every Google email address you will connect. With Gmail, Calendar, or Drive access, testing-mode refresh tokens
          expire after seven days. For ongoing sync, review the Publish app option and any verification requirements Google shows;
          otherwise expect to reconnect weekly.
        </li>
        <li>
          <strong>Configure data access.</strong> Under{' '}
          <NewTabLink href="https://console.cloud.google.com/auth/scopes">Data Access</NewTabLink> → Add or remove scopes,
          add userinfo.email and userinfo.profile, plus the read scopes for the services you need:
          gmail.readonly, calendar.readonly, and drive.readonly. Add gmail.send only if you want Memory to send email.
          Search by these scope names, then update and save. You will choose the integrations again when connecting in Memory.
        </li>
        <li>
          <strong>Create an OAuth client.</strong> Open{' '}
          <NewTabLink href="https://console.cloud.google.com/auth/clients">Clients</NewTabLink> → Create client.
          Choose Web application and give it a name. Under Authorized redirect URIs, click Add URI and enter:
          <code className="block break-all rounded bg-slate-100 p-2 mt-2 select-all">{redirectUri}</code>
          Use the public HTTPS address of this Memory instance (HTTP localhost is suitable for local development).
          The scheme, hostname, port, and path must match exactly. Leave Authorized JavaScript origins empty for this server-side flow.
          Use this as the client’s only redirect URI; Memory uses the first URI in the uploaded file.
        </li>
        <li>
          <strong>Download the JSON file.</strong> Click Create, then Download JSON in the client-created dialog.
          Keep the downloaded client credentials file; Google may only offer the client secret when it is created.
          This must be an OAuth client file, not a service-account key. Keep it private: it contains the client secret.
        </li>
        <li>
          <strong>Upload and connect.</strong> Return here and click Upload credentials JSON (or Replace config).
          Select the downloaded file, then click Connect account, select the Google permissions you need, and finish signing in.
          To start ingestion, add your Gmail source under Email, choose folders under Drive, or add a Calendar source.
          If an API was just enabled, allow a few minutes before retrying Sync.
        </li>
      </ol>
      <p className="mt-3">
        Google’s guides:{' '}
        <NewTabLink href="https://developers.google.com/identity/protocols/oauth2/web-server#creatingcred">Create web application credentials</NewTabLink>{' '}
        and <NewTabLink href="https://developers.google.com/identity/protocols/oauth2#expiration">refresh-token expiration</NewTabLink>.
      </p>
    </Disclosure>
  )
}
