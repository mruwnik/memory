import { expect, it } from 'vitest'
import { fileUrl } from './files'

it.each([
  ['notes.txt', '/files/notes.txt'],
  ['books/dune.epub', '/files/books/dune.epub'],
  ['mail/re: #42?.eml', '/files/mail/re%3A%20%2342%3F.eml'],
  ['reports/100%.pdf', '/files/reports/100%25.pdf'],
  ['a/b c/ü.png', '/files/a/b%20c/%C3%BC.png'],
])('links %j as %s', (path, url) => {
  expect(fileUrl(path)).toBe(url)
})
