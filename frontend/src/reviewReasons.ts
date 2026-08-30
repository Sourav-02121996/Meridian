// Shared between WorkspaceView.tsx and BatchesPage.tsx — previously duplicated
// verbatim in both files. Keys mirror backend/app/models.py's Job.review_reason
// comment (the full list of values the scheduler can set).
export const reviewReasonLabels: Record<string, string> = {
  below_threshold: 'Below auto-apply threshold',
  unsupported_multi_step: 'Multi-step application not supported (e.g. Workday)',
  no_resume_file: 'No resume PDF on file',
  custom_questions: 'Form has custom questions',
  navigation_timeout: 'Application page took too long to load',
  form_not_found: 'Could not find the application form',
  submit_not_found: 'Could not find a submit button',
  fields_invalid_before_submit: 'A field looked filled but the form rejected it — never submitted',
  submission_rejected: 'Submitted, but the form rejected it — never went through',
  submission_request_failed: 'Submission request failed before reaching the employer',
  listing_closed: 'This posting closed while Meridian was applying — never submitted',
  confirmation_not_detected: "Submitted, but couldn't confirm success",
  captcha_protected: 'Skipped — this employer requires solving a CAPTCHA',
  unexpected_error: 'Unexpected error while applying',
};
