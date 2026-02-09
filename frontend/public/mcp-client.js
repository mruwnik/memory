/**
 * MCP Client Library for HTML Reports
 *
 * Usage:
 *   <script src="/ui/mcp-client.js"></script>
 *   <script>
 *     MCP.call('people_list_all', { limit: 100 }).then(people => {
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
  async function call(method, params = {}) {
    const accessToken = getCookie('access_token');
    if (!accessToken) {
      throw new Error('Not authenticated - no access_token cookie found');
    }

    const response = await fetch(`/mcp/${method}`, {
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

  // Export MCP namespace
  window.MCP = {
    call,
    batch,

    // Commonly used shortcuts
    people: {
      list: (params) => call('people_list_all', params),
      fetch: (identifier, params) => call('people_fetch', { identifier, ...params }),
    },

    github: {
      listIssues: (params) => call('github_list_entities', { type: 'issue', ...params }),
      listMilestones: (params) => call('github_list_entities', { type: 'milestone', ...params }),
      listProjects: (params) => call('github_list_entities', { type: 'project', ...params }),
      fetch: (type, params) => call('github_fetch', { type, ...params }),
    },

    discord: {
      send: (params) => call('discord_send_message', params),
      listChannels: (params) => call('discord_list_channels', params),
    },

    search: (query, params) => call('core_search', { query, ...params }),
  };

  console.log('MCP Client loaded. Use MCP.call(method, params) to make calls.');
})(window);
