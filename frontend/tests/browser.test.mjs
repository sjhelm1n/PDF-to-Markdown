import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { after, before, test } from 'node:test'
import { randomUUID } from 'node:crypto'
import { copyFileSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const url = process.env.PDF2MD_BROWSER_URL ?? 'http://127.0.0.1:5173'
const pdf = process.env.PDF2MD_TEST_PDF
const executable = process.env.AGENT_BROWSER_BIN ?? 'agent-browser'
const session = `pdf2md-${randomUUID().slice(0, 8)}`

function browser(...args) {
  const output = execFileSync(executable, ['--session', session, '--json', ...args], {
    encoding: 'utf8',
    timeout: 35_000,
  })
  const response = JSON.parse(output)
  assert.ok(response.success, response.error)
  return response.data
}

const evaluate = (expression) => browser('eval', expression).result
const wait = (expression) => browser('wait', '--fn', expression)
const button = (label) => `Array.from(document.querySelectorAll('button')).find(button => button.textContent.includes(${JSON.stringify(label)}))`
const click = (label) => evaluate(`${button(label)}.click()`)
const previewImage = 'document.querySelector(".md-content img")'

function checkPreviewImage() {
  assert.ok(evaluate(`${previewImage} !== null`), 'Expected a rendered figure')
  evaluate(`${previewImage}.scrollIntoView()`)
  wait(`${previewImage}.complete && ${previewImage}.naturalWidth > 0`)
  assert.ok(evaluate(`Array.from(document.querySelectorAll('.md-content img')).every(image => image.getAttribute('src').startsWith('/api/jobs/'))`))
}

function appendEdit(markdown) {
  evaluate(`(() => {
    const editor = document.querySelector('textarea');
    const setValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
    setValue.call(editor, editor.value + ${JSON.stringify(markdown)});
    editor.dispatchEvent(new InputEvent('input', {bubbles: true}));
  })()`)
}

before(() => {
  browser('open', url)
  wait('document.querySelector("main") !== null')
})

after(() => browser('close'))

test('workspace loads and guards empty or invalid uploads', () => {
  assert.equal(evaluate('document.title'), 'PDF to Markdown')
  assert.equal(evaluate(`${button('Convert PDF')}.disabled`), true)
  assert.equal(evaluate(`${button('Download ZIP')}.disabled`), true)
  assert.equal(evaluate('document.querySelector("vite-error-overlay") === null'), true)
  evaluate(`(() => {
    const event = new DataTransfer();
    event.items.add(new File(['invalid'], 'not-a-pdf.txt'));
    document.querySelector('.upload').dispatchEvent(new DragEvent('drop', {bubbles: true, dataTransfer: event}));
  })()`)
  wait('document.body.innerText.includes("Please choose a PDF.")')
  assert.equal(evaluate(`${button('Convert PDF')}.disabled`), true)
})

test('real Docling page editing, preview, and export preserve the complete draft', { skip: !pdf }, t => {
  const directory = mkdtempSync(join(tmpdir(), 'pdf2md-browser-'))
  t.after(() => rmSync(directory, { recursive: true, force: true }))
  const upload = join(directory, 'Café 泵 Manual.PDF')
  copyFileSync(resolve(pdf), upload)
  wait('document.querySelector("#conversion-engine").value === "docling"')
  assert.equal(evaluate('document.querySelector("option[value=nemotron]").disabled'), true)
  browser('upload', 'input[type=file]', upload)
  wait('document.querySelector(".react-pdf__Page canvas") !== null')
  assert.equal(evaluate(`${button('Convert PDF')}.disabled`), false)
  const conversionStart = Date.now()
  click('Convert PDF')
  const deadline = Date.now() + 300_000
  let state
  while (Date.now() < deadline) {
    state = evaluate(`({
      complete: document.querySelector('[role=status]').textContent.includes('Conversion complete'),
      error: document.querySelector('[role=alert]')?.textContent,
    })`)
    if (state.complete || state.error) break
    browser('wait', '1000')
  }
  assert.equal(state?.complete, true, state?.error ?? 'Docling conversion timed out')
  assert.ok(evaluate(`(() => {
    const [pdfPane, markdownPane] = document.querySelectorAll('.panes article');
    return pdfPane.getBoundingClientRect().right < markdownPane.getBoundingClientRect().left;
  })()`))
  assert.ok(evaluate('document.querySelector(".md-content").textContent.trim().length > 0'))
  console.log(`Visible Markdown preview after ${((Date.now() - conversionStart) / 1000).toFixed(2)} s`)
  assert.ok(evaluate("document.body.innerText.includes('Page-aligned Markdown preview')"))
  const pdfPane = 'document.querySelector(".pane-body.pdf")'
  const markdownPane = 'document.querySelector(".pane-body.markdown")'
  if (evaluate(`[${pdfPane}, ${markdownPane}].every(pane => pane.scrollHeight - pane.clientHeight > 50)`)) {
    evaluate(`${pdfPane}.scrollTop = ${pdfPane}.scrollHeight`)
    wait(`${markdownPane}.scrollTop > 50`)
    evaluate(`${markdownPane}.scrollTop = 0`)
    wait(`${pdfPane}.scrollTop === 0`)
  }
  const first = evaluate('document.querySelector(".md-content").textContent')
  click('Next')
  wait(`document.querySelector('.pane-foot').textContent.includes('Page 2 of')`)
  assert.notEqual(evaluate('document.querySelector(".md-content").textContent'), first)
  evaluate(`(() => {
    const select = document.querySelector('[aria-label="Markdown source page"]');
    select.value = '1';
    select.dispatchEvent(new Event('change', {bubbles: true}));
  })()`)
  wait(`document.querySelector('.pane-foot').textContent.includes('Page 1 of')`)
  assert.equal(evaluate('document.querySelector(".md-content").textContent'), first)

  const divider = 'document.querySelector(".pane-divider")'
  evaluate(`${divider}.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true}))`)
  wait(`${divider}.getAttribute('aria-valuenow') === '55'`)

  assert.equal(evaluate('document.querySelector(".check input").checked'), true)
  for (let page = 1; !evaluate(previewImage + ' !== null') && !evaluate(`${button('Next')}.disabled`); page++) {
    click('Next')
    wait(`document.querySelector('.pane-foot').textContent.includes('Page ${page + 1} of')`)
  }
  assert.ok(evaluate("document.body.innerText.includes('Page-aligned Markdown preview')"))
  checkPreviewImage()
  const jobUrl = evaluate(`${previewImage}.getAttribute('src').split('/assets/')[0]`)
  const source = evaluate(`(async () => (await fetch(${JSON.stringify(jobUrl)})).json())()`)
  assert.deepEqual(source.images.map(image => image.path),
    source.images.map((_, index) => `images/Café-泵-Manual-fig-${String(index + 1).padStart(6, '0')}.png`))
  const loadedImage = evaluate(`decodeURIComponent(${previewImage}.getAttribute('src').split('/assets/')[1])`)
  assert.ok(source.images.some(image => image.path === `images/${loadedImage}`))
  const expectedPages = source.pages.slice()
  const fullMarkdown = pages => pages.map((page, index) =>
    source.page_anchors ? `<!-- source-page: ${index + 1} -->\n\n${page}` : page).join('\n\n')
  const canEditPages = expectedPages.every(page => page.trim()) && fullMarkdown(expectedPages) === source.markdown
  while (!evaluate(`${button('Previous')}.disabled`)) click('Previous')
  wait(`document.querySelector('.pane-foot').textContent.includes('Page 1 of')`)

  click('Edit Markdown')
  wait('document.querySelector("textarea") !== null')
  const editor = 'document.querySelector("textarea")'
  if (canEditPages) {
    const pageOneEdit = '\n\n## Local validation page one edit\n'
    const pageTwoEdit = '\n\n## Local validation page two edit\n'
    assert.equal(evaluate(`${editor}.getAttribute('aria-label')`), 'Markdown editor for page 1')
    assert.equal(evaluate(`${editor}.value`), expectedPages[0])
    appendEdit(pageOneEdit)
    expectedPages[0] += pageOneEdit

    click('Next')
    wait(`${editor}.getAttribute('aria-label') === 'Markdown editor for page 2'`)
    assert.equal(evaluate(`${editor}.value`), expectedPages[1])
    appendEdit(pageTwoEdit)
    expectedPages[1] += pageTwoEdit
    click('Preview')
    wait('document.querySelector(".md-content h2:last-child")?.textContent === "Local validation page two edit"')
    assert.equal(evaluate('document.querySelector(".md-content").textContent.includes("Local validation page one edit")'), false)
    click('Previous')
    wait('document.querySelector(".md-content").textContent.includes("Local validation page one edit")')
    click('Save edits')
    wait(`!${button('Download ZIP')}.disabled`)
    assert.ok(evaluate('document.querySelector(".md-content").textContent.includes("Local validation page one edit")'))
    click('Edit Markdown')
    wait(`${editor} !== null`)
    assert.equal(evaluate(`${editor}.value`), expectedPages[0])
  } else {
    assert.ok(evaluate('document.querySelector(".markdown-editor .hint").textContent.includes("Editing the full document")'))
  }

  const linkedToggle = 'document.querySelector(".check input")'
  evaluate(`${linkedToggle}.click()`)
  wait(`${editor}.getAttribute('aria-label') === 'Markdown editor'`)
  const fullBeforeEdit = canEditPages ? fullMarkdown(expectedPages) : source.markdown
  assert.equal(evaluate(`${editor}.value`), fullBeforeEdit)
  const fullEdit = '\n\n## Local validation full document edit\n'
  appendEdit(fullEdit)
  evaluate(`${linkedToggle}.click()`)
  wait('document.body.innerText.includes("Unsaved edits")')
  const unloadAllowed = 'window.dispatchEvent(new Event("beforeunload", {cancelable: true}))'
  assert.equal(evaluate(unloadAllowed), false)
  assert.equal(evaluate(`${button('Download ZIP')}.disabled`), true)
  evaluate(`window.confirm = message => { window.lastConfirmation = message; return false; }`)
  click('Convert PDF')
  assert.match(evaluate('window.lastConfirmation'), /discard unsaved edits and reconvert/i)
  assert.equal(evaluate(`${editor}.value`), fullBeforeEdit + fullEdit)
  click('Preview')
  wait('document.querySelector(".md-content") !== null')
  assert.ok(evaluate(`document.querySelector('.md-content').textContent.includes('Local validation full document edit')`),
    'Preview must show the current draft even with page linking selected')
  click('Edit Markdown')
  click('Save edits')
  wait(`!${button('Download ZIP')}.disabled`)
  assert.equal(evaluate(unloadAllowed), true)
  click('Preview')
  wait(`document.querySelector('.md-content')?.textContent.includes('Local validation full document edit')`)
  assert.equal(evaluate('document.querySelector(".check input").checked'), true)
  assert.ok(evaluate('document.querySelector(".md-content .hint")?.textContent.includes("Page linking is paused")'))
  assert.ok(evaluate('document.querySelector(".pane:last-child .pane-foot").textContent.includes("Full document preview")'))
  checkPreviewImage()
  const exportResponse = evaluate(`(async () => {
    const jobUrl = ${JSON.stringify(jobUrl)};
    const job = await (await fetch(jobUrl)).json();
    const response = await fetch(jobUrl + '/download');
    const bytes = new Uint8Array(await response.arrayBuffer());
    return {
      savedDraftMatches: job.markdown === ${JSON.stringify(fullBeforeEdit + fullEdit)},
      status: response.status, type: response.headers.get('content-type'), magic: Array.from(bytes.slice(0, 4)),
    };
  })()`)
  assert.deepEqual(exportResponse, { savedDraftMatches: true, status: 200, type: 'application/zip', magic: [80, 75, 3, 4] })

  browser('reload')
  wait('document.querySelector(".md-content")?.textContent.includes("Local validation full document edit")')
  wait('document.querySelector(".react-pdf__Page canvas") !== null')
  assert.equal(evaluate(`${divider}.getAttribute('aria-valuenow')`), '55')
  assert.equal(evaluate('document.querySelector(".md-content").textContent.includes("source-page:")'), false)
  assert.ok(evaluate('document.querySelector(".pane:last-child .pane-foot").textContent.includes("Full document preview")'))
  assert.equal(evaluate(`${button('Download ZIP')}.disabled`), false)
})

test('a corrupt PDF surfaces a Docling failure and leaves export disabled', { skip: !pdf }, () => {
  browser('open', url)
  wait('document.querySelector("#conversion-engine")?.value === "docling"')
  evaluate(`(() => {
    const event = new DataTransfer();
    event.items.add(new File(['%PDF-1.4\\ncorrupt document\\n%%EOF\\n'], 'corrupt.pdf', {type: 'application/pdf'}));
    document.querySelector('.upload').dispatchEvent(new DragEvent('drop', {bubbles: true, dataTransfer: event}));
  })()`)
  wait(`!${button('Convert PDF')}.disabled`)
  click('Convert PDF')
  wait(`Array.from(document.querySelectorAll('[role=alert]')).some(alert => /Conversion failed|without pages/.test(alert.textContent))`)
  assert.equal(evaluate(`${button('Download ZIP')}.disabled`), true)
})
