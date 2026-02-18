/**
 * MCP Client Library for HTML Reports
 *
 * Provides access to ALL MCP tools via authenticated API calls.
 *
 * Usage:
 *   <script src="/ui/mcp-client.js"></script>
 *   <script>
 *     // Generic - works with ANY MCP method
 *     MCP.call('people_list_all', { limit: 100 }).then(people => {
 *       console.log('Got people:', people);
 *     });
 *
 *     // Or use convenience shortcuts for common methods
 *     MCP.people.list({ limit: 100 }).then(people => {
 *       console.log('Got people:', people);
 *     });
 *   </script>
 */

(function(window) {
  'use strict';

  // Helper to get cookie value
  function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return null;
  }

  // Parse Server-Sent Events response
  async function parseSSE(response) {
    const text = await response.text();
    const lines = text.split('\n');
    let data = '';

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        data = line.slice(6);
        break;
      }
    }

    if (data) {
      return JSON.parse(data);
    }
    throw new Error('No data in SSE response');
  }

  // Make MCP call
  async function call(method, params = {}, options = {}) {
    const accessToken = getCookie('access_token');
    if (!accessToken) {
      throw new Error('Not authenticated - no access_token cookie found');
    }

    // Support external MCP servers via serverUrl option
    const serverUrl = options.serverUrl || `/mcp/${method}`;
    const fullUrl = serverUrl.startsWith('http')
      ? serverUrl  // External server - use full URL
      : serverUrl; // Local server - relative URL

    const response = await fetch(fullUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream',
        'Authorization': `Bearer ${accessToken}`
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: Date.now(),
        method: 'tools/call',
        params: { name: method, arguments: params }
      })
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const contentType = response.headers.get('content-type');
    const data = contentType && contentType.includes('text/event-stream')
      ? await parseSSE(response)
      : await response.json();

    if (data?.result?.isError) {
      const errorMsg = data.result.content[0]?.text || 'MCP call failed';
      throw new Error(errorMsg);
    }

    // Extract the actual result from MCP response format
    const result = data?.result?.content.map(item => {
      try { return JSON.parse(item.text); }
      catch { return item.text; }
    })[0];

    return result;
  }

  // Batch multiple calls in parallel
  async function batch(calls) {
    return Promise.all(
      calls.map(({ method, params }) => call(method, params))
    );
  }

  // MCP session cache for external servers
  const mcpSessions = {};

  // Initialize MCP session for external server
  async function initializeMcpSession(serverUrl) {
    const accessToken = getCookie('access_token');
    if (!accessToken) {
      throw new Error('Not authenticated - no access_token cookie found');
    }

    console.log('[MCP] Initializing session for:', serverUrl);

    const response = await fetch(serverUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream',
        'Authorization': `Bearer ${accessToken}`
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: Date.now(),
        method: 'initialize',
        params: {
          protocolVersion: '2024-11-05',
          capabilities: {},
          clientInfo: {
            name: 'memory-mcp-client',
            version: '1.0.0'
          }
        }
      })
    });

    console.log('[MCP] Initialize response status:', response.status);
    console.log('[MCP] Response headers:', Array.from(response.headers.entries()));

    if (!response.ok) {
      throw new Error(`MCP initialize failed: ${response.status}`);
    }

    // Extract session ID from response header
    const sessionId = response.headers.get('mcp-session-id');
    console.log('[MCP] Session ID from header:', sessionId);

    if (!sessionId) {
      console.error('[MCP] Available headers:', Array.from(response.headers.keys()));
      throw new Error('MCP server did not return session ID');
    }

    console.log('[MCP] Session initialized successfully:', sessionId);
    return sessionId;
  }

  // Call external MCP server (with session management)
  async function callExternal(serverUrl, method, params = {}) {
    // Get or create session for this server
    if (!mcpSessions[serverUrl]) {
      mcpSessions[serverUrl] = await initializeMcpSession(serverUrl);
    }

    const accessToken = getCookie('access_token');
    const response = await fetch(serverUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream',
        'Authorization': `Bearer ${accessToken}`,
        'Mcp-Session-Id': mcpSessions[serverUrl]
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: Date.now(),
        method: 'tools/call',
        params: { name: method, arguments: params }
      })
    });

    if (!response.ok) {
      // Session might have expired, try reinitializing once
      if (response.status === 400 || response.status === 401) {
        delete mcpSessions[serverUrl];
        return callExternal(serverUrl, method, params); // Retry with new session
      }
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const contentType = response.headers.get('content-type');
    const data = contentType && contentType.includes('text/event-stream')
      ? await parseSSE(response)
      : await response.json();

    if (data?.result?.isError) {
      const errorMsg = data.result.content[0]?.text || 'MCP call failed';
      throw new Error(errorMsg);
    }

    // Extract the actual result from MCP response format
    const result = data?.result?.content.map(item => {
      try { return JSON.parse(item.text); }
      catch { return item.text; }
    })[0];

    return result;
  }

  // Export MCP namespace
  window.MCP = {
    call,  // Generic method - supports ALL MCP tools
    callExternal, // Call external MCP servers
    batch, // Batch multiple calls in parallel

    // Books
    books: {
      list: (params) => call('books_list_books', params),
      fetch: (book_id, params) => call('books_fetch', { book_id, ...params }),
    },

    // Core (Search & Knowledge Base)
    search: (query, params) => call('core_search', { query, ...params }),
    searchObservations: (query, params) => call('core_search_observations', { query, ...params }),
    observe: (params) => call('core_observe', params),
    fetch: (params) => call('core_fetch', params),
    fetchFile: (filename) => call('core_fetch_file', { filename }),
    listItems: (params) => call('core_list_items', params),
    countItems: (params) => call('core_count_items', params),

    // Discord
    discord: {
      send: (params) => call('discord_send_message', params),
      channelHistory: (params) => call('discord_channel_history', params),
      listChannels: (params) => call('discord_list_channels', params),
      listRoles: (params) => call('discord_list_roles', params),
      listRoleMembers: (params) => call('discord_list_role_members', params),
      listCategories: (params) => call('discord_list_categories', params),
      roleAddUser: (params) => call('discord_role_add_user', params),
      roleRemoveUser: (params) => call('discord_role_remove_user', params),
      createRole: (params) => call('discord_create_role', params),
      perms: (params) => call('discord_perms', params),
      setPerms: (params) => call('discord_set_perms', params),
      delPerms: (params) => call('discord_del_perms', params),
      upsertChannel: (params) => call('discord_upsert_channel', params),
      upsertCategory: (params) => call('discord_upsert_category', params),
      deleteChannel: (params) => call('discord_delete_channel', params),
      deleteCategory: (params) => call('discord_delete_category', params),
    },

    // Email
    email: {
      send: (params) => call('email_send', params),
    },

    // Forecasting
    forecast: {
      getForecasts: (term, params) => call('forecast_get_forecasts', { term, ...params }),
      clearCache: () => call('forecast_clear_cache', {}),
      history: (market_id, source, params) => call('forecast_history', { market_id, source, ...params }),
      getMarketDepth: (market_id, params) => call('forecast_get_market_depth', { market_id, ...params }),
      compare: (term, params) => call('forecast_compare_forecasts', { term, ...params }),
      resolved: (params) => call('forecast_resolved', params),
      watch: (market_id, source, params) => call('forecast_watch_market', { market_id, source, ...params }),
      getWatchlist: () => call('forecast_get_watchlist', {}),
      unwatch: (market_id, source) => call('forecast_unwatch_market', { market_id, source }),
    },

    // GitHub
    github: {
      listIssues: (params) => call('github_list_entities', { type: 'issue', ...params }),
      listMilestones: (params) => call('github_list_entities', { type: 'milestone', ...params }),
      listProjects: (params) => call('github_list_entities', { type: 'project', ...params }),
      listTeams: (params) => call('github_list_entities', { type: 'team', ...params }),
      fetch: (type, params) => call('github_fetch', { type, ...params }),
      upsertIssue: (params) => call('github_upsert_issue', params),
      addTeamMember: (params) => call('github_add_team_member', params),
      removeTeamMember: (params) => call('github_remove_team_member', params),
      commentOnIssue: (params) => call('github_comment_on_issue', params),
    },

    // Journal
    journal: {
      add: (params) => call('journal_add', params),
      listAll: (target_id, params) => call('journal_list_all', { target_id, ...params }),
    },

    // Meta
    meta: {
      getMetadataSchemas: () => call('meta_get_metadata_schemas', {}),
      getCurrentTime: () => call('meta_get_current_time', {}),
      getUser: (params) => call('meta_get_user', params),
      notifyUser: (params) => call('meta_notify_user', params),
    },

    // Notes
    notes: {
      upsert: (params) => call('notes_upsert', params),
      listFiles: (params) => call('notes_note_files', params),
    },

    // Organizer (Tasks & Calendar)
    organizer: {
      upcoming: (params) => call('organizer_upcoming', params),
      listTasks: (params) => call('organizer_list_tasks', params),
      fetch: (task_id, params) => call('organizer_fetch', { task_id, ...params }),
      createTask: (params) => call('organizer_create_task', params),
      updateTask: (params) => call('organizer_update_task', params),
    },

    // People
    people: {
      upsert: (params) => call('people_upsert', params),
      fetch: (identifier, params) => call('people_fetch', { identifier, ...params }),
      list: (params) => call('people_list_all', params),
      delete: (identifier) => call('people_delete', { identifier }),
      merge: (identifiers, params) => call('people_merge', { identifiers, ...params }),
      tidbitAdd: (params) => call('people_tidbit_add', params),
      tidbitUpdate: (params) => call('people_tidbit_update', params),
      tidbitDelete: (tidbit_id) => call('people_tidbit_delete', { tidbit_id }),
      tidbitList: (identifier, params) => call('people_tidbit_list', { identifier, ...params }),
    },

    // Polling
    polling: {
      upsert: (params) => call('polling_upsert_poll', params),
      list: (params) => call('polling_list_polls', params),
      delete: (poll_id) => call('polling_delete_poll', { poll_id }),
      fetch: (params) => call('polling_fetch', params),
    },

    // Projects
    projects: {
      listAll: (params) => call('projects_list_all', params),
      fetch: (project_id, params) => call('projects_fetch', { project_id, ...params }),
      upsert: (params) => call('projects_upsert', params),
      delete: (project_id) => call('projects_delete', { project_id }),
    },

    // Reports
    reports: {
      upsert: (params) => call('reports_upsert', params),
      delete: (report_id) => call('reports_delete', { report_id }),
    },

    // Scheduler
    scheduler: {
      listAll: (params) => call('scheduler_list_all', params),
      upsert: (params) => call('scheduler_upsert', params),
      cancel: (task_id) => call('scheduler_cancel', { task_id }),
      delete: (task_id) => call('scheduler_delete', { task_id }),
      executions: (task_id, params) => call('scheduler_executions', { task_id, ...params }),
    },

    // Slack
    slack: {
      send: (params) => call('slack_send', params),
      addReaction: (params) => call('slack_add_reaction', params),
      listChannels: (params) => call('slack_list_channels', params),
      getChannelHistory: (params) => call('slack_get_channel_history', params),
    },

    // Teams
    teams: {
      upsert: (params) => call('teams_upsert', params),
      fetch: (team, params) => call('teams_fetch', { team, ...params }),
      listAll: (params) => call('teams_list_all', params),
      addMember: (params) => call('teams_team_add_member', params),
      removeMember: (params) => call('teams_team_remove_member', params),
      projectListAccess: (project) => call('teams_project_list_access', { project }),
    },
  };

  console.log('MCP Client loaded. Use MCP.call() for local tools or MCP.callExternal(serverUrl, method, params) for external MCP servers.');
})(window);
