'use client'

import type { ReactNode } from 'react'

import { NumberScrub } from '@/components/ui/number-scrub'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { SwatchPicker } from '@/components/ui/swatch-picker'
import { Switch } from '@/components/ui/switch'
import type { CompositionV1 } from '@/lib/api/generated/model'

import { FontPicker } from './FontPicker'
import { LookCard } from './LookCard'
import { TEMPLATES, type TemplateDefinition } from './templates'
import { useBrandColors } from './use-brand-colors'

type CaptionStyle = CompositionV1['captions']['style']

/** The weights every shipped font face carries. */
const WEIGHTS = [300, 400, 500, 600, 700, 800, 900] as const

/**
 * The look of the captions: templates first, then every style field, then motion.
 *
 * Applying a template writes its type and colour into the composition and records the
 * exact template version it came from, so a template that changes later never rewrites a
 * Revision somebody already approved. The composition has no vertical-position field, so
 * no position control is offered.
 */
export function StylePanel({
  composition,
  onStyle,
  onApplyTemplate,
  motion,
}: {
  composition: CompositionV1
  onStyle: (patch: Partial<CaptionStyle>) => void
  onApplyTemplate: (template: TemplateDefinition) => void
  motion: ReactNode
}) {
  const style = composition.captions.style
  const brandColors = useBrandColors(composition.brandKit)
  const applied = composition.template

  return (
    <div className="space-y-6">
      <section aria-label="Templates" className="space-y-3">
        <h2 className="text-title">Templates</h2>
        <div className="grid grid-cols-3 gap-2">
          {TEMPLATES.map((template) => (
            <LookCard
              key={`${template.id}-${template.version}`}
              template={template}
              applied={
                applied !== null &&
                applied.id === template.id &&
                applied.version === template.version
              }
              onApply={() => onApplyTemplate(template)}
            />
          ))}
        </div>
      </section>

      <section aria-label="Caption style" className="space-y-4">
        <h2 className="text-title">Caption style</h2>
        <FontPicker value={style.fontFamily} onChange={(fontFamily) => onStyle({ fontFamily })} />
        <NumberScrub
          label="Size"
          accessibleName="Caption size"
          value={style.fontSize}
          min={12}
          max={200}
          step={1}
          unit="px"
          onCommit={(fontSize) => onStyle({ fontSize })}
        />
        <SegmentedControl
          label="Caption weight"
          size="sm"
          value={String(style.weight)}
          options={WEIGHTS.map((weight) => ({ value: String(weight), label: String(weight) }))}
          onChange={(weight) => onStyle({ weight: Number(weight) })}
          className="flex-wrap"
        />
        <div className="flex items-center justify-between">
          <span className="text-caption text-muted-foreground">Italic</span>
          <Switch
            aria-label="Caption italic"
            checked={style.italic}
            onCheckedChange={(italic) => onStyle({ italic })}
          />
        </div>
        <SegmentedControl<CaptionStyle['align']>
          label="Caption alignment"
          size="sm"
          value={style.align}
          options={[
            { value: 'left', label: 'Left' },
            { value: 'center', label: 'Centre' },
            { value: 'right', label: 'Right' },
          ]}
          onChange={(align) => onStyle({ align })}
        />
        <SegmentedControl<CaptionStyle['decoration']>
          label="Caption decoration"
          size="sm"
          value={style.decoration}
          options={[
            { value: 'none', label: 'None' },
            { value: 'underline', label: 'Underline' },
            { value: 'strikethrough', label: 'Strikethrough' },
          ]}
          onChange={(decoration) => onStyle({ decoration })}
        />
        <SwatchPicker
          label="Colour"
          accessibleName="Caption colour"
          value={style.color}
          brandColors={brandColors}
          onChange={(color) => onStyle({ color })}
        />
        <SwatchPicker
          label="Highlight"
          accessibleName="Caption highlight colour"
          value={style.highlightColor}
          brandColors={brandColors}
          onChange={(highlightColor) => onStyle({ highlightColor })}
        />
        <div className="flex items-center justify-between">
          <span className="text-caption text-muted-foreground">Background</span>
          <Switch
            aria-label="Caption background"
            checked={style.backgroundEnabled}
            onCheckedChange={(backgroundEnabled) => onStyle({ backgroundEnabled })}
          />
        </div>
        {style.backgroundEnabled ? (
          <SwatchPicker
            label="Background colour"
            accessibleName="Caption background colour"
            value={style.backgroundColor}
            brandColors={brandColors}
            onChange={(backgroundColor) => onStyle({ backgroundColor })}
          />
        ) : null}
        <NumberScrub
          label="Letter spacing"
          accessibleName="Caption letter spacing"
          value={style.letterSpacing}
          min={-10}
          max={40}
          step={0.5}
          precision={1}
          unit="px"
          onCommit={(letterSpacing) => onStyle({ letterSpacing })}
        />
        <NumberScrub
          label="Line height"
          accessibleName="Caption line height"
          value={style.lineHeight}
          min={0.5}
          max={3}
          step={0.1}
          precision={1}
          onCommit={(lineHeight) => onStyle({ lineHeight })}
        />
      </section>

      {motion}
    </div>
  )
}
