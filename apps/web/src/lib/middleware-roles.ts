/**
 * Roles the merchant console accepts, in one place.
 *
 * Split out of `middleware.ts` because `sessionToken.ts` needs the same list and
 * importing the middleware from the verifier would create a cycle -- the
 * middleware imports the verifier.
 *
 * These values are duplicated from `ROLE_SCOPES` in
 * `packages/security/principals.py`. The duplication is safe in the direction that
 * matters: a role added here but not recognised by the API grants nothing,
 * because every endpoint still calls `require_roles`. The failure mode of drift
 * is a legitimate operator seeing a sign-in prompt, never a privilege gain.
 */
export const CONSOLE_ROLES = new Set([
  "merchant_admin",
  "merchant_operator",
  "platform_admin",
]);
