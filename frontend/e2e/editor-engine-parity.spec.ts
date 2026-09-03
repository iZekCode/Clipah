import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type Page } from '@playwright/test'
import { build } from 'esbuild'

import { GATES, type EngineMeasurement, type GateVerdict } from '../spikes/editor-engines/benchmark'

/**
 * The bake-off measurements, taken where they mean something.
 *
 * Every number here is read inside a real browser tab: the engines are bundled and
 * injected into the page, and the timings come from that page's `performance`, not from
 * the Node process running Playwright. A run that cannot decode the proxy fails; a gate
 * this environment cannot observe is reported unmeasured rather than passed.
 */

const SPIKES = resolve(dirname(fileURLToPath(import.meta.url)), '../spikes/editor-engines')
const PROXY_URL = process.env.CLIPAH_EDITOR_PROXY_URL
const REFERENCE_MACHINE = process.env.CLIPAH_EDITOR_REFERENCE_MACHINE

/** What the bundle exposes on `window` once it is injected. */
interface EngineBundle {
  createElahEngine: (options: unknown) => unknown
  createOpenReelMediabunnyEngine: (options: unknown) => unknown
  measureEngine: (
    engine: string,
    create: () => unknown,
    composition: unknown,
    frames: number[],
    environment: unknown,
  ) => Promise<EngineMeasurement>
  browserEnvironment: () => unknown
  longProxyComposition: (minutes: number) => unknown
  seekPlan: (composition: unknown, samples: number) => number[]
  verdict: (measurement: EngineMeasurement) => GateVerdict[]
}

declare global {
  interface Window {
    ClipahEngines: EngineBundle
  }
}

let bundle: string | null = null

/** Bundle the spike once per run, the way a browser would receive it. */
async function engineBundle(): Promise<string> {
  if (bundle === null) {
    const built = await build({
      stdin: {
        contents: `
          export { createElahEngine } from './elah-adapter'
          export { createOpenReelMediabunnyEngine } from './openreel-mediabunny-adapter'
          export {
            measureEngine, browserEnvironment, longProxyComposition, seekPlan, verdict,
          } from './benchmark'
        `,
        resolveDir: SPIKES,
        loader: 'ts',
      },
      bundle: true,
      format: 'iife',
      globalName: 'ClipahEngines',
      platform: 'browser',
      target: 'es2022',
      write: false,
    })
    const output = built.outputFiles[0]
    if (output === undefined) {
      throw new Error('the engine bundle produced no output')
    }
    bundle = output.text
  }
  return bundle
}

/**
 * Count the workers this browser is actually running.
 *
 * Only Chromium can answer, and only through the DevTools protocol, so this is asked
 * from the Node side rather than from the page. Anything else returns `null`, and the
 * gate is then reported unmeasured rather than passed.
 */
async function countWorkers(page: Page, browserName: string): Promise<number | null> {
  if (browserName !== 'chromium') {
    return null
  }
  const session = await page.context().newCDPSession(page)
  try {
    const { targetInfos } = await session.send('Target.getTargets')
    return targetInfos.filter((target) => target.type.includes('worker')).length
  } finally {
    await session.detach()
  }
}

/**
 * Name the graphics stack that produced these numbers.
 *
 * A headless browser usually falls back to software rendering, and a GPU-composited
 * engine measured on a software rasteriser is being measured on the wrong machine. The
 * renderer string goes into the record so no number is ever read without it.
 */
async function graphicsRenderer(page: Page): Promise<string> {
  return page.evaluate(() => {
    const gl = document.createElement('canvas').getContext('webgl2')
    if (gl === null) {
      return 'no webgl2'
    }
    const info = gl.getExtension('WEBGL_debug_renderer_info')
    const renderer =
      info === null ? gl.getParameter(gl.RENDERER) : gl.getParameter(info.UNMASKED_RENDERER_WEBGL)
    return String(renderer)
  })
}

/** Open a page with both engines available on `window`. */
async function pageWithEngines(page: Page): Promise<void> {
  // A tab that dies mid-measurement must say so. Without this a crash surfaces only as
  // "target closed", which names neither the engine's fault nor the harness's.
  page.on('crash', () => console.log('[bake-off] the page crashed'))
  page.on('pageerror', (error) => console.log(`[bake-off] page error: ${error.message}`))
  page.on('console', (message) => {
    if (message.type() === 'error') {
      console.log(`[bake-off] console error: ${message.text().slice(0, 200)}`)
    }
  })
  await page.goto('/')
  await page.addScriptTag({ content: await engineBundle() })
}

// A candidate that is slow must produce a slow number, not a timeout: a timed-out run
// says nothing about which gate failed or by how much. This has to be configured on the
// suite — `test.setTimeout` at module scope is ignored, and the default 30 s cut a slow
// engine off mid-measurement and reported it as a closed page.
test.describe.configure({ timeout: 900_000 })

test.beforeEach(() => {
  test.skip(
    PROXY_URL === undefined || REFERENCE_MACHINE === undefined,
    'Set CLIPAH_EDITOR_PROXY_URL to a signed 30-minute-or-longer proxy and ' +
      'CLIPAH_EDITOR_REFERENCE_MACHINE to the machine the thresholds were fixed for.',
  )
})

for (const minutes of [30, 60]) {
  for (const engine of ['elah', 'openreel-mediabunny'] as const) {
    test(`${engine} holds a ${minutes}-minute timeline inside every gate`, async ({
      page,
      browserName,
    }) => {
      await pageWithEngines(page)
      const renderer = await graphicsRenderer(page)
      const workersBefore = await countWorkers(page, browserName)

      const observed = await page.evaluate(
        async ([name, proxyUrl, length]) => {
          const engines = window.ClipahEngines
          const create = () => {
            const options = { resolveAsset: () => proxyUrl as string }
            return name === 'elah'
              ? engines.createElahEngine(options)
              : engines.createOpenReelMediabunnyEngine(options)
          }
          const composition = engines.longProxyComposition(length as number)
          return engines.measureEngine(
            name as string,
            create,
            composition,
            engines.seekPlan(composition, 20),
            engines.browserEnvironment(),
          )
        },
        [engine, PROXY_URL, minutes] as const,
      )

      // `dispose()` has already run inside the measurement, so anything still alive here
      // is something the engine left behind.
      const workersAfter = await countWorkers(page, browserName)
      const measurement: EngineMeasurement = {
        ...observed,
        leakedWorkers:
          workersBefore === null || workersAfter === null ? null : workersAfter - workersBefore,
      }
      // Recorded whatever the gates say, so a failing run still reports what it saw.
      await test.info().attach(`${engine}-${minutes}min-${browserName}`, {
        body: JSON.stringify(
          { referenceMachine: REFERENCE_MACHINE, graphicsRenderer: renderer, ...measurement },
          null,
          2,
        ),
        contentType: 'application/json',
      })
      console.log(
        `[bake-off] ${engine} ${minutes}min ${browserName}: ` +
          `time to usable ${Math.round(measurement.timeToUsableMs)}ms ` +
          `(load ${Math.round(measurement.loadMs)}ms + first frame ${Math.round(measurement.firstFrameMs)}ms), ` +
          `median seek ${Math.round(measurement.medianSeekMs)}ms, ` +
          `p95 seek ${Math.round(measurement.p95SeekMs)}ms, ` +
          `heap ${measurement.peakHeapBytes === null ? 'unobserved' : `${Math.round(measurement.peakHeapBytes / 1024 / 1024)}MiB`}, ` +
          `leaked workers ${measurement.leakedWorkers ?? 'unobserved'}, ` +
          `decoded ${measurement.decodedFrames}/${measurement.seekSamples}, ` +
          `renderer ${renderer}`,
      )

      expect(
        measurement.failures,
        `${engine} could not decode every sampled frame`,
      ).toEqual([])
      expect(measurement.decodedFrames).toBe(measurement.seekSamples)

      const gates = await page.evaluate(
        (result) => window.ClipahEngines.verdict(result),
        measurement,
      )
      for (const gate of gates) {
        // Chromium alone exposes a worker count and a heap reading. Requiring either
        // elsewhere would turn a browser limitation into a false claim about the engine,
        // and claiming one anyway would be worse; so outside Chromium they must be
        // reported unmeasured, and the ADR records which browser produced which gate.
        const chromiumOnly = gate.gate === 'maxLeakedWorkers' || gate.gate === 'maxHeapBytes'
        if (chromiumOnly && browserName !== 'chromium') {
          expect(
            gate.unmeasured,
            `${gate.gate} cannot be observed in ${browserName} and must not be claimed`,
          ).toBe(true)
          continue
        }
        expect(
          gate.unmeasured,
          `${gate.gate} was not observed on ${REFERENCE_MACHINE}; it cannot be claimed`,
        ).toBe(false)
        expect(gate.observed, `${gate.gate} exceeded ${gate.limit}`).toBeLessThanOrEqual(gate.limit)
      }
      expect(measurement.timeToUsableMs).toBeLessThanOrEqual(GATES.maxLoadMs)
    })
  }
}

test('a browser without WebCodecs degrades instead of failing silently', async ({
  page,
  browserName,
}) => {
  await pageWithEngines(page)

  const outcome = await page.evaluate(async (proxyUrl) => {
    const engines = window.ClipahEngines
    const engine = engines.createOpenReelMediabunnyEngine({
      resolveAsset: () => proxyUrl as string,
    }) as {
      load: (composition: unknown) => Promise<void>
      renderPreviewFrame: (frame: number) => Promise<{ decoded: boolean }>
      dispose: () => Promise<void>
    }
    await engine.load(engines.longProxyComposition(1))
    try {
      const frame = await engine.renderPreviewFrame(30)
      return { supported: typeof VideoDecoder !== 'undefined', decoded: frame.decoded, refusal: null }
    } catch (error) {
      return {
        supported: typeof VideoDecoder !== 'undefined',
        decoded: false,
        refusal: error instanceof Error ? error.message : String(error),
      }
    } finally {
      await engine.dispose()
    }
  }, PROXY_URL)

  // Where the decoder exists the frame must arrive; where it does not, the engine must
  // say so in words rather than return a blank picture as though it had worked.
  if (outcome.supported) {
    expect(outcome.decoded, `${browserName} has WebCodecs but decoded nothing`).toBe(true)
  } else {
    expect(outcome.refusal, `${browserName} lacks WebCodecs and reported no refusal`).toMatch(
      /webcodecs/i,
    )
  }
})

// Frame and timing parity against the native FFmpeg renderer needs the fixture render
// Task 24 produces and the documented font mask. Neither exists yet, so the comparison is
// not written here as a test that would pass by doing nothing.
test.fixme('every engine matches the FFmpeg fixture within one frame and SSIM 0.97', async () => {})
