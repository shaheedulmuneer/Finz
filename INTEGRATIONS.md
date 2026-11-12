# Official integration foundations

**Status:** Documentation and UI placeholders only. The app does NOT connect to these services yet. Never enter client account passwords in source code.

## Instagram / Facebook — Meta

- Register a Meta developer application and select the correct Instagram API product and login flow for the business accounts that authorize the app.
- Request approved scopes for permitted insights, media and account information; review requirements change and must be confirmed in the official Meta developer portal.
- Save the OAuth state and PKCE/CSRF values server-side; exchange authorization codes on the server; encrypt tokens at rest.
- Sync permitted media and account insights to a normalized workspace-specific table, noting metric granularity and date windows.
- For competitor information use permitted professional-account Business Discovery where available; never assume all profiles, exact view counts or every public Reel are available.
- References: https://developers.facebook.com/docs/instagram-platform/ and https://developers.facebook.com/docs/graph-api/

## TikTok

- Get developer app approval and user authorization. Display API can provide profile and recent video metadata for an authorized user under appropriate scopes (`user.info.basic`, `video.list`). Rich analytics may require separate access/products.
- Reference: https://developers.tiktok.com/docs/en/display-api-get-started

## Google Business Profile

- Obtain OAuth consent for authorized client locations and relevant performance API permissions/quotas.
- Import relevant daily profile visibility and interaction metrics into separate tables, preserving metric meanings and units. Search impressions are not people or customers.
- Reference: https://developers.google.com/my-business/reference/performance/rest

## WhatsApp Business

- Use Meta's WhatsApp Cloud API with authorized business assets, approved templates where needed and secure webhook verification.
- Retain only required lead data, obtain consent and implement retention/deletion.
- Reference: https://developers.facebook.com/docs/whatsapp/cloud-api/

## Architecture interfaces to implement

1. `oauth_connections(workspace_id,provider,external_account_id,encrypted_access_token,encrypted_refresh_token,scopes,expires_at,updated_at)`
2. `sync_runs(workspace_id,provider,started_at,finished_at,status,error_message,records_added)`
3. Provider adapters exposing `authorize`, `refresh`, `fetch_account_metrics`, `fetch_media`, `normalize_records`.
4. Daily jobs for permitted authorized account insights. Enforce provider rate limits and de-duplicate by provider event ID.
5. A privacy policy and request-to-delete route before supporting outside customers.

**Never promise unrestricted competitor monitoring:** public Instagram content may be hidden behind sign-in, may be removed, and may be unavailable to the permitted APIs. Present only verified results and their direct source URLs.
