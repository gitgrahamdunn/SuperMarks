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

    await page.goto(`/submissions/${seed.question_level_submission_id}/mark?examId=${seed.exam_id}`);
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
  });

  test('front-page totals lane can build a PDF preview, confirm seeded totals, and move into results', async ({ page }) => {
    const seed = readSeed();

    await page.goto(`/submissions/${seed.front_page_submission_id}/front-page-totals?examId=${seed.exam_id}`);
    await expect(page).toHaveURL(new RegExp(`/submissions/${seed.front_page_submission_id}/front-page-totals\\?examId=${seed.exam_id}`));
    const previewImage = page.getByRole('img', { name: /Page 1 for Jordan/i });
    await expect(previewImage).toBeVisible();
    await expect(previewImage).toHaveAttribute('src', /^blob:/);
    await expect(page.getByText('Jordan Lee')).toBeVisible();
    await expect(page.getByText('42 / 50')).toBeVisible();
    await expect(page.getByText('Outcome OB1')).toBeVisible();
    await page.getByRole('button', { name: 'Accept parsed read' }).click();
    await expect(page).toHaveURL(new RegExp(`/exams/${seed.exam_id}$`));

    await page.goto(`/submissions/${seed.front_page_submission_id}/results?examId=${seed.exam_id}`);

    await expect(page).toHaveURL(new RegExp(`/submissions/${seed.front_page_submission_id}/results\\?examId=${seed.exam_id}`));
    await expect(page.getByText('42 / 50')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Front-page confirmation' })).toBeVisible();
    await expect(page.getByText('OB1: 18/20', { exact: true })).toBeVisible();
  });
});
