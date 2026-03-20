import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const metadataPath = path.resolve(here, '../../../artifacts/acceptance/seed-metadata.json');

type SeedMetadata = {
  exam_id: number;
  question_level_submission_id: number;
  front_page_submission_id: number;
  question_ids: Record<string, number>;
};

function readSeed(): SeedMetadata {
  return JSON.parse(fs.readFileSync(metadataPath, 'utf8')) as SeedMetadata;
}

test.describe('seeded workflow-lane acceptance', () => {
  test('question-level lane can resume teacher marking and reach results', async ({ page }) => {
    const seed = readSeed();

    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Exams' })).toBeVisible();
    await page.getByRole('link', { name: 'Open workspace' }).first().click();

    await expect(page.getByRole('heading', { name: 'Acceptance Seed Exam' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Front-page totals queue' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Question-level queue' })).toBeVisible();
    await expect(page.getByText('Front-page totals lane').first()).toBeVisible();
    await expect(page.getByText('Question-level lane').first()).toBeVisible();
    await expect(page.getByText('1 front-page totals submission: Front-page totals still need teacher confirmation.')).toBeVisible();
    await expect(page.getByText('1 question-level submission: Result needs teacher attention before it is ready for export.')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Weakest complete: Avery' })).toBeVisible();
    await expect(page.getByText('still need marking or prep.')).toHaveCount(0);

    const averyReportingRow = page.getByRole('row', { name: /Avery/ }).first();
    await expect(averyReportingRow.getByText('Result needs teacher attention before it is ready for export.')).toBeVisible();
    await expect(averyReportingRow.getByText('Return point: Q2')).toBeVisible();
    await expect(averyReportingRow.getByRole('link', { name: 'Resume: Q2' })).toBeVisible();

    const averyQueueRow = page.getByRole('row', { name: /Avery/ }).filter({ hasText: 'Question-level lane' }).last();
    await expect(averyQueueRow.getByText('Resume marking at Q2.')).toBeVisible();
    await expect(averyQueueRow.getByText('Result needs teacher attention before it is ready for export.')).toBeVisible();

    await page.getByRole('link', { name: /Resume at Q2/i }).click();
    await expect(page).toHaveURL(new RegExp(`/submissions/${seed.question_level_submission_id}/mark\\?examId=${seed.exam_id}`));
    await expect(page.getByRole('heading', { name: 'Mark submission' })).toBeVisible();
    await expect(page.getByText('Needs entry: 1')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Q2' })).toBeVisible();

    const marksInput = page.getByLabel('Marks awarded');
    await marksInput.fill('6');
    await page.getByLabel('Teacher note').fill('Solved the equation cleanly.');
    await page.getByRole('button', { name: 'Save + next needs entry' }).click();

    await expect(page.getByText('Marking complete.')).toBeVisible();
    await expect(page.getByText('Teacher-marked: 2')).toBeVisible();
    await page.getByRole('link', { name: 'Open results' }).first().click();

    await expect(page).toHaveURL(new RegExp(`/submissions/${seed.question_level_submission_id}/results\\?examId=${seed.exam_id}`));
    await expect(page.getByText('10 / 10')).toBeVisible();
    await expect(page.getByText('ALG1: 4/4', { exact: true })).toBeVisible();
    await expect(page.getByText('ALG2: 6/6', { exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Q2' })).toBeVisible();

    await page.goto(`/exams/${seed.exam_id}`);
    const completedAveryReportingRow = page.getByRole('row', { name: /Avery/ }).first();
    await expect(completedAveryReportingRow.getByText('Teacher-entered question marks are complete and ready for export.')).toBeVisible();
    await expect(completedAveryReportingRow.getByText('Export-ready')).toBeVisible();
  });

  test('front-page totals lane can confirm seeded totals and move into results', async ({ page }) => {
    const seed = readSeed();

    await page.goto(`/exams/${seed.exam_id}`);
    await expect(page.getByRole('heading', { name: 'Acceptance Seed Exam' })).toBeVisible();

    const jordanReportingRow = page.getByRole('row', { name: /Jordan/ }).first();
    await expect(jordanReportingRow.getByText('Front-page totals still need teacher confirmation.')).toBeVisible();
    await expect(jordanReportingRow.getByText('Totals not yet confirmed')).toBeVisible();
    await expect(jordanReportingRow.getByRole('link', { name: 'Open totals capture' })).toBeVisible();

    const jordanQueueRow = page.getByRole('row', { name: /Jordan/ }).filter({ hasText: 'Front-page totals lane' }).last();
    await expect(jordanQueueRow.getByText('Capture and confirm the front-page totals.').first()).toBeVisible();
    await expect(jordanQueueRow.getByText('Front-page totals still need teacher confirmation.').first()).toBeVisible();

    await page.getByRole('link', { name: 'Open totals capture' }).first().click();

    await expect(page).toHaveURL(new RegExp(`/submissions/${seed.front_page_submission_id}/front-page-totals\\?examId=${seed.exam_id}`));
    await expect(page.getByRole('heading', { name: 'Jordan' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Extractor candidates' })).toBeVisible();
    await expect(page.getByText('mock-front-page-totals').first()).toBeVisible();

    await expect(page.getByLabel('Overall total')).toHaveValue('42');
    await expect(page.getByText('Student name mismatch').first()).toBeVisible();

    const noteField = page.getByLabel('Teacher note');
    await noteField.fill('Confirmed from the paper front page.');
    await page.getByRole('button', { name: /^Confirm totals$/ }).click();

    await expect(page.getByText('Front-page totals saved and confirmed.')).toBeVisible();
    await page.getByRole('link', { name: 'Open results' }).first().click();

    await expect(page).toHaveURL(new RegExp(`/submissions/${seed.front_page_submission_id}/results\\?examId=${seed.exam_id}`));
    await expect(page.getByText('42 / 50')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Front-page confirmation' })).toBeVisible();
    await expect(page.getByText('OB1: 18/20', { exact: true })).toBeVisible();
    await expect(page.getByText('Confirmed from the paper front page.')).toBeVisible();

    await page.goto(`/exams/${seed.exam_id}`);
    const confirmedJordanReportingRow = page.getByRole('row', { name: /Jordan/ }).first();
    await expect(confirmedJordanReportingRow.getByText('Confirmed front-page totals will export as the authoritative result.')).toBeVisible();
    await expect(confirmedJordanReportingRow.getByText('Export-ready')).toBeVisible();
  });
});
