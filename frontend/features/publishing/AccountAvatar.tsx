import type { SocialAccountResponse } from '@/lib/api/generated/model'

import { providerLabel } from './gates'

/** The account's own picture, or its provider's initial when it has none. */
export function AccountAvatar({ account }: { account: SocialAccountResponse | undefined }) {
  if (account?.avatarUrl) {
    return (
      // Provider avatar URLs are not known to the Next image optimizer.
      // eslint-disable-next-line @next/next/no-img-element
      <img src={account.avatarUrl} alt="" className="size-7 shrink-0 rounded-full object-cover" />
    )
  }
  return (
    <span
      aria-hidden="true"
      className="flex size-7 shrink-0 items-center justify-center rounded-full bg-secondary font-mono text-caption text-muted-foreground"
    >
      {providerLabel(account?.provider ?? '').charAt(0) || '?'}
    </span>
  )
}
