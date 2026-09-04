'use client'

import { TEMPLATES, type TemplateDefinition } from './templates'
import type { CompositionV1 } from '@/lib/api/generated/model'

/**
 * The looks a member can apply in one action.
 *
 * Applying one writes its type and colour into the composition and records the exact
 * template version it came from. A template that changes later can therefore never
 * rewrite a Revision somebody already approved: the document already holds the look.
 */
export function TemplatesPanel({
  composition,
  onApply,
}: {
  composition: CompositionV1
  onApply: (template: TemplateDefinition) => void
}) {
  const applied = composition.template

  return (
    <section aria-label="Templates" className="flex flex-col gap-2 rounded-lg border p-3 text-xs">
      <h2 className="text-sm font-medium">Templates</h2>
      <ul className="flex flex-col gap-2">
        {TEMPLATES.map((template) => {
          const current =
            applied !== null && applied.id === template.id && applied.version === template.version
          return (
            <li key={`${template.id}-${template.version}`}>
              <button
                type="button"
                aria-pressed={current}
                onClick={() => onApply(template)}
                className={`w-full rounded border p-2 text-left ${
                  current ? 'border-primary bg-primary/10' : ''
                }`}
              >
                <span className="font-medium">{template.name}</span>
                <span className="block text-muted-foreground">{template.description}</span>
                <span className="block text-muted-foreground">
                  {template.captionStyle.fontFamily} · {template.captionMode} captions · version{' '}
                  {template.version}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
