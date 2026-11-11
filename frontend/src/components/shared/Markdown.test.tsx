import { describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { isOwnImage, Markdown } from '@/components/shared/Markdown'
import { SearchResult } from '@/components/search/results'

const TRACKER = 'https://tracker.example/open.gif?to=victim%40corp.example'
const ORIGIN = window.location.origin

describe('isOwnImage', () => {
  it.each([
    ['/files/emails/a.png', true],
    [`${ORIGIN}/files/a.png`, true],
    ['data:image/png;base64,AAAA', true],
    ['blob:https://x/1', true],
    [TRACKER, false],
    ['//tracker.example/a.gif', false],
    ['http://[bad', false],
  ])('%s -> %s', (src, own) => {
    expect(isOwnImage(src)).toBe(own)
  })
})

describe('Markdown images', () => {
  it('does not load a remote image until asked', () => {
    const { container } = render(<Markdown>{`Hi\n\n![pixel](${TRACKER})`}</Markdown>)
    expect(container.querySelector('img')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Load image "pixel" from tracker.example' }))

    const image = container.querySelector('img')
    expect([image?.getAttribute('src'), image?.getAttribute('referrerpolicy')]).toEqual([TRACKER, 'no-referrer'])
  })

  it('loads an image stored on this server', () => {
    const { container } = render(<Markdown>{'![chart](/files/reports/chart.png)'}</Markdown>)
    expect(container.querySelector('img')?.getAttribute('src')).toBe('/files/reports/chart.png')
  })

  it('loading an image inside a link does not follow the link', () => {
    const { container } = render(<Markdown>{`[![x](${TRACKER})](https://elsewhere.example/)`}</Markdown>)
    const click = new MouseEvent('click', { bubbles: true, cancelable: true })

    act(() => { screen.getByRole('button').dispatchEvent(click) })

    expect(click.defaultPrevented).toBe(true)
    expect(container.querySelector('img')).not.toBeNull()
  })

  it.each(['message/rfc822', 'text/markdown', 'application/pdf'])('search results for %s hold remote images back', mime => {
    const { container } = render(
      <SearchResult result={{ filename: null, content: `![](${TRACKER})`, chunks: [], tags: [], mime_type: mime, metadata: null }} />,
    )
    // PDFs keep their source in a closed disclosure; open everything.
    container.querySelectorAll('details').forEach(d => { d.open = true })

    expect(container.querySelector(`img[src^="https://tracker.example"]`)).toBeNull()
    expect(screen.getByRole('button', { name: /Load image from tracker\.example/, hidden: true })).toBeTruthy()
  })
})
