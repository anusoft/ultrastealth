#!/usr/bin/env bun
/**
 * Watsons Thailand catalog crawler.
 *
 * Data source discovered with the craft-scraper flow:
 *   GET https://api.watsons.co.th/api/v2/wtcth/products/search
 *   GET https://api.watsons.co.th/api/v2/wtcth/products/{code}
 *   GET https://api.watsons.co.th/api/v2/wtcth/products/{code}/reviews
 *
 * Runtime: Bun only. No browser at runtime; Watsons' Akamai edge rejects plain
 * curl/fetch, so this uses scrapling-js header generation plus wreq-js TLS
 * impersonation.
 *
 * Fresh-machine bootstrap from this script's directory:
 *   curl -fsSL https://raw.githubusercontent.com/anusoft/ultrastealth/main/install.sh | bash
 *   curl -fsSL https://raw.githubusercontent.com/anusoft/scrapling-js/main/install.sh | bash
 */

import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";

const SITE_ORIGIN = "https://www.watsons.co.th";
const API_ORIGIN = "https://api.watsons.co.th";
const BASE_SITE = "wtcth";
const CURRENCY = "THB";
const LISTING_PAGE_SIZE = 20;
const REVIEW_PAGE_SIZE = 10;
const LOCAL_SCRAPLING_DIR =
  process.env.SCRAPLING_JS_LOCAL || "/Users/mac/Projects/scrapling-js";

function printHelp() {
  console.log(`Usage: bun run watsons.scrapling-js.js [options]

Options:
  --start-url URL          Watsons URL to use as referer/locale seed
                           default: https://www.watsons.co.th/
  --out DIR                output directory
                           default: out/watsons-products
  --category-limit N       number of discovered categories to crawl
                           default: 2
  --page-limit N           listing pages per category
                           default: 1
  --product-limit N        products per category
                           default: 3
  --review-page-limit N    review pages per product
                           default: 1
  --review-limit N|all     reviews saved per product; Watsons can ignore pageSize
                           default: 50
  --delay MS               polite delay between product/detail calls
                           default: 100
  --resume                 skip product JSON files already written
  -h, --help               show this help

Fresh-machine setup:
  curl -fsSL https://raw.githubusercontent.com/anusoft/ultrastealth/main/install.sh | bash
  curl -fsSL https://raw.githubusercontent.com/anusoft/scrapling-js/main/install.sh | bash`);
}

function positiveInt(name, raw) {
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 1) {
    throw new Error(`${name} must be a positive integer, got ${raw}`);
  }
  return n;
}

function positiveLimit(name, raw) {
  if (raw === "all") return Infinity;
  return positiveInt(name, raw);
}

function parseArgs(argv) {
  const opts = {
    startUrl: "https://www.watsons.co.th/",
    out: "out/watsons-products",
    categoryLimit: 2,
    pageLimit: 1,
    productLimit: 3,
    reviewPageLimit: 1,
    reviewLimit: 50,
    delayMs: 100,
    resume: false,
  };

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--help" || arg === "-h") {
      printHelp();
      process.exit(0);
    } else if (arg === "--start-url") {
      opts.startUrl = argv[++i];
    } else if (arg === "--out") {
      opts.out = argv[++i];
    } else if (arg === "--category-limit") {
      opts.categoryLimit = positiveInt(arg, argv[++i]);
    } else if (arg === "--page-limit") {
      opts.pageLimit = positiveInt(arg, argv[++i]);
    } else if (arg === "--product-limit") {
      opts.productLimit = positiveInt(arg, argv[++i]);
    } else if (arg === "--review-page-limit") {
      opts.reviewPageLimit = positiveInt(arg, argv[++i]);
    } else if (arg === "--review-limit") {
      opts.reviewLimit = positiveLimit(arg, argv[++i]);
    } else if (arg === "--delay") {
      const n = Number(argv[++i]);
      if (!Number.isFinite(n) || n < 0) throw new Error(`--delay must be >= 0`);
      opts.delayMs = n;
    } else if (arg === "--resume") {
      opts.resume = true;
    } else {
      throw new Error(`unknown arg: ${arg}`);
    }
  }

  return opts;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function loadRuntime() {
  let scrapling;
  let wreq;

  try {
    scrapling = await import("scrapling-js");
  } catch (first) {
    try {
      scrapling = await import(`${LOCAL_SCRAPLING_DIR}/dist/index.js`);
    } catch {
      throw first;
    }
  }

  try {
    wreq = await import("wreq-js");
  } catch (first) {
    try {
      wreq = await import(`${LOCAL_SCRAPLING_DIR}/node_modules/wreq-js/dist/wreq-js.js`);
    } catch {
      throw first;
    }
  }

  if (typeof scrapling.generateChromeHeaders !== "function") {
    throw new Error("scrapling-js generateChromeHeaders() was not found");
  }
  if (typeof wreq.fetch !== "function") {
    throw new Error("wreq-js fetch() was not found");
  }

  return {
    generateChromeHeaders: scrapling.generateChromeHeaders,
    fetch: wreq.fetch,
  };
}

function localeFromStartUrl(startUrl) {
  try {
    const url = new URL(startUrl);
    const first = url.pathname.split("/").filter(Boolean)[0];
    return first === "en" ? "en" : "th";
  } catch {
    return "th";
  }
}

function apiUrl(path, params = {}) {
  const url = new URL(path, API_ORIGIN);
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
  }
  return url.toString();
}

function siteUrl(path, locale = "th") {
  if (!path) return `${SITE_ORIGIN}/${locale}/`;
  if (/^https?:\/\//i.test(path)) return path;
  const normalized = path.startsWith("/") ? path : `/${path}`;
  if (normalized.startsWith(`/${locale}/`)) return `${SITE_ORIGIN}${normalized}`;
  return `${SITE_ORIGIN}/${locale}${normalized}`;
}

async function requestText(runtime, url, referer, accept = "application/json") {
  const generated = runtime.generateChromeHeaders({ url, referer });
  const response = await runtime.fetch(url, {
    browser: `chrome_${generated.version}`,
    os: generated.os,
    headers: {
      ...generated.headers,
      accept,
      referer,
    },
  });

  const text = await response.text();
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} for ${url}: ${text.slice(0, 300)}`);
  }
  return text;
}

async function getJSON(runtime, url, referer) {
  const text = await requestText(runtime, url, referer, "application/json");
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`Non-JSON response for ${url}: ${text.slice(0, 300)}`);
  }
}

async function writeJSON(path, value) {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, JSON.stringify(value, null, 2));
}

async function readJSON(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

async function fileExists(path) {
  try {
    await readFile(path);
    return true;
  } catch {
    return false;
  }
}

function flattenCategoryHierarchy(root) {
  const out = [];
  const seen = new Set();
  const walk = (node) => {
    if (!node || !node.code || seen.has(node.code)) return;
    seen.add(node.code);
    out.push({
      code: String(node.code),
      name: node.name ?? "",
      count: Number(node.count ?? 0),
      level: Number(node.level ?? 0),
      query: node.query ?? null,
      url: node.url ?? null,
    });
    for (const child of node.subCategories || []) walk(child);
  };
  walk(root);
  return out;
}

async function discoverCategories(runtime, opts) {
  const locale = localeFromStartUrl(opts.startUrl);
  const url = apiUrl(`/api/v2/${BASE_SITE}/products/search`, {
    fields: "FULL",
    query: "::category:1",
    currentPage: 0,
    pageSize: 1,
    filterOOS: true,
    lang: locale,
    curr: CURRENCY,
  });
  const data = await getJSON(runtime, url, siteUrl("/", locale));
  const categories = flattenCategoryHierarchy(data.productCategoryHierarchy)
    .filter((category) => category.code !== "1" && category.count > 0)
    .map((category) => ({
      ...category,
      url: siteUrl(category.url || `/lc/${category.code}`, locale),
    }));

  if (categories.length === 0) {
    throw new Error("No Watsons categories discovered from productCategoryHierarchy");
  }
  return { categories, rootSearch: data };
}

async function fetchListing(runtime, opts, category, pageIndex) {
  const locale = localeFromStartUrl(opts.startUrl);
  const url = apiUrl(`/api/v2/${BASE_SITE}/products/search`, {
    fields: "PRODUCT_CAROUSEL",
    query: `::category:${category.code}`,
    currentPage: pageIndex,
    pageSize: LISTING_PAGE_SIZE,
    filterOOS: true,
    lang: locale,
    curr: CURRENCY,
  });
  return getJSON(runtime, url, category.url || siteUrl(`/lc/${category.code}`, locale));
}

async function fetchProductDetail(runtime, opts, productCode, productUrl) {
  const locale = localeFromStartUrl(opts.startUrl);
  const url = apiUrl(`/api/v2/${BASE_SITE}/products/${encodeURIComponent(productCode)}`, {
    fields: "FULL",
    lang: locale,
    curr: CURRENCY,
  });
  return getJSON(runtime, url, productUrl || siteUrl(`/p/${productCode}`, locale));
}

async function fetchReviewPage(runtime, opts, productCode, productUrl, pageIndex) {
  const locale = localeFromStartUrl(opts.startUrl);
  const url = apiUrl(`/api/v2/${BASE_SITE}/products/${encodeURIComponent(productCode)}/reviews`, {
    currentPage: pageIndex,
    pageSize: REVIEW_PAGE_SIZE,
    lang: locale,
    curr: CURRENCY,
  });
  return getJSON(runtime, url, productUrl || siteUrl(`/p/${productCode}`, locale));
}

function normalizePrice(value) {
  if (!value) return null;
  return {
    value: typeof value.value === "number" ? value.value : null,
    formatted: value.formattedValue ?? null,
    currency: value.currencyIso ?? CURRENCY,
    type: value.priceType ?? null,
  };
}

function qualifierMap(qualifiers = []) {
  return Object.fromEntries(
    qualifiers
      .filter((q) => q && q.qualifier)
      .map((q) => [q.qualifier, q.value ?? null])
  );
}

function normalizeVariant(option) {
  if (!option) return null;
  const qualifiers = qualifierMap(option.variantOptionQualifiers || []);
  return {
    code: option.code ?? null,
    url: option.url ? siteUrl(option.url) : null,
    price: normalizePrice(option.priceData),
    stock: option.stock ?? null,
    qualifiers,
    size: qualifiers.elabVariantSize ?? null,
    color: qualifiers.elabColorDescription ?? null,
  };
}

function normalizeReview(review) {
  return {
    id: review.id ?? null,
    rating: typeof review.rating === "number" ? review.rating : null,
    date: review.date ?? null,
    alias: review.alias ?? null,
    headline: review.headline ?? null,
    comment: review.comment ?? null,
    approvalStatus: review.approvalStatus ?? null,
  };
}

function htmlText(value) {
  if (!value) return null;
  return String(value)
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/p>/gi, "\n")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\s+\n/g, "\n")
    .replace(/\n\s+/g, "\n")
    .replace(/[ \t]{2,}/g, " ")
    .trim() || null;
}

function normalizeFeature(feature) {
  if (!feature) return null;
  return {
    code: feature.code ?? null,
    name: feature.name ?? null,
    comparable: feature.comparable ?? null,
    values: (feature.featureValues || [])
      .map((value) => value?.value ?? value?.name ?? value)
      .filter((value) => value !== undefined && value !== null && value !== ""),
  };
}

function normalizeSpecifications(product) {
  return (product.classifications || [])
    .flatMap((classification) => classification.features || [])
    .map(normalizeFeature)
    .filter(Boolean);
}

function limitReviewPage(reviewData, remaining) {
  const allReviews = reviewData.reviews || [];
  const limit = Number.isFinite(remaining) ? remaining : allReviews.length;
  return {
    ...reviewData,
    reviews: allReviews.slice(0, limit),
    availableReviewCount: allReviews.length,
    reviewLimitApplied: Number.isFinite(remaining) && allReviews.length > remaining,
  };
}

function imageUrls(product) {
  const urls = new Set();
  for (const image of product.images || []) {
    if (image?.url) urls.add(siteUrl(image.url));
  }
  if (product.thumbnailImage?.url) urls.add(siteUrl(product.thumbnailImage.url));
  return [...urls];
}

function normalizeProduct({ detail, listing, category, reviews }) {
  const product = detail || listing || {};
  const variants = [
    ...(product.variantOptions || []).map(normalizeVariant),
    ...(product.baseOptions || []).map((base) => normalizeVariant(base.selected)),
  ].filter(Boolean);
  const reviewItems = reviews.flatMap((page) => page.reviews || []).map(normalizeReview);
  const descriptionHtml = product.description ?? null;
  const shortDescriptionHtml = product.shortDescription ?? null;
  const summaryHtml = product.summary ?? null;
  const pricing = {
    current: normalizePrice(product.price ?? listing?.price),
    elab: normalizePrice(product.elabPrice ?? listing?.elabPrice),
    old: normalizePrice(product.elabOldPrice ?? listing?.elabOldPrice ?? product.strikeThroughPrice),
    markdown: normalizePrice(product.elabMarkDownPrice ?? listing?.elabMarkDownPrice),
    range: product.priceRange ?? listing?.priceRange ?? null,
  };

  return {
    code: product.code ?? listing?.code ?? null,
    name: product.name ?? listing?.name ?? null,
    gtmName: product.gtmName ?? listing?.gtmName ?? null,
    brand: product.masterBrand?.name ?? product.brand?.name ?? listing?.masterBrand?.name ?? null,
    brandCode: product.masterBrand?.code ?? product.brand?.code ?? listing?.masterBrand?.code ?? null,
    url: siteUrl(product.url ?? listing?.url ?? `/p/${product.code ?? listing?.code ?? ""}`),
    sourceCategory: {
      code: category.code,
      name: category.name,
      url: category.url,
    },
    categoryPath: product.categoryNameLevels || product.categories || listing?.categoryNameLevels || [],
    contentSizeUnit: product.contentSizeUnit ?? listing?.contentSizeUnit ?? null,
    descriptionHtml,
    descriptionText: htmlText(descriptionHtml),
    shortDescriptionHtml,
    shortDescriptionText: htmlText(shortDescriptionHtml),
    summaryHtml,
    summaryText: htmlText(summaryHtml),
    price: pricing.current,
    salePrice: pricing.elab,
    oldPrice: pricing.old,
    pricing,
    averageRating: product.averageRating ?? product.reviewAvgRating ?? listing?.averageRating ?? null,
    reviewCountHint:
      product.productNumberOfReview ?? product.numberOfReviews ?? listing?.productNumberOfReview ?? null,
    purchasable: product.purchasable ?? listing?.purchasable ?? null,
    stock: product.stock ?? listing?.stock ?? null,
    images: imageUrls(product),
    variants,
    variantCount: variants.length,
    hasVariants: variants.length > 0,
    classifications: product.classifications ?? [],
    specifications: normalizeSpecifications(product),
    featureIcons: product.elabFeatureIcons ?? [],
    identityBadgeIcons: product.identityBadgeIcons ?? [],
    promotions: product.elabPromotions ?? product.promotionTags ?? listing?.promotionTags ?? [],
    reviews: {
      pagesFetched: reviews.length,
      fetchedCount: reviewItems.length,
      items: reviewItems,
    },
    raw: {
      listing,
      detail,
      reviewPages: reviews,
    },
  };
}

async function run(opts) {
  const started = Date.now();
  const runtime = await loadRuntime();
  const locale = localeFromStartUrl(opts.startUrl);

  const dirs = {
    categoryPages: join(opts.out, "category-pages"),
    products: join(opts.out, "products"),
    reviews: join(opts.out, "reviews"),
  };
  await Promise.all(Object.values(dirs).map((dir) => mkdir(dir, { recursive: true })));

  const { categories, rootSearch } = await discoverCategories(runtime, opts);
  await writeJSON(join(opts.out, "categories.json"), categories);
  await writeJSON(join(opts.out, "root-search.json"), rootSearch);

  const selectedCategories = categories.slice(0, opts.categoryLimit);
  console.log(
    `discovered ${categories.length} categories; selected ${selectedCategories.length}: ` +
      selectedCategories.map((c) => `${c.code} ${c.name}`).join(", ")
  );

  const summary = {
    locale,
    categoriesDiscovered: categories.length,
    categoriesSelected: selectedCategories.length,
    categoryPagesFetched: 0,
    categoryPagesReused: 0,
    productsListed: 0,
    productDetailsFetched: 0,
    productDetailsSkipped: 0,
    reviewPagesFetched: 0,
    reviewItems: 0,
    reviewItemsAvailable: 0,
    output: opts.out,
  };

  const existingProductFiles = opts.resume
    ? new Set(await readdir(dirs.products).catch(() => []))
    : new Set();

  for (const category of selectedCategories) {
    let listedForCategory = 0;
    let totalPages = opts.pageLimit;
    for (let pageIndex = 0; pageIndex < totalPages && pageIndex < opts.pageLimit; pageIndex++) {
      const pageName = `${category.code}-page-${String(pageIndex).padStart(4, "0")}.json`;
      const pagePath = join(dirs.categoryPages, pageName);
      let listing;
      if (opts.resume && await fileExists(pagePath)) {
        listing = await readJSON(pagePath);
        summary.categoryPagesReused++;
      } else {
        listing = await fetchListing(runtime, opts, category, pageIndex);
        await writeJSON(pagePath, listing);
        summary.categoryPagesFetched++;
      }

      const pagination = listing.pagination || {};
      totalPages = Math.min(
        opts.pageLimit,
        Number.isInteger(pagination.totalPages) ? pagination.totalPages : opts.pageLimit
      );
      const products = listing.products || [];
      console.log(
        `category ${category.code} page ${pageIndex + 1}/${totalPages}: ` +
          `${products.length} products (total=${pagination.totalResults ?? "unknown"})`
      );
      if (products.length === 0) break;

      for (const listingProduct of products) {
        if (listedForCategory >= opts.productLimit) break;
        summary.productsListed++;
        listedForCategory++;

        const code = listingProduct.code;
        if (!code) continue;
        const productUrl = siteUrl(listingProduct.url, locale);
        const productFile = `${code}.json`;
        const productPath = join(dirs.products, productFile);

        if (opts.resume && existingProductFiles.has(productFile)) {
          summary.productDetailsSkipped++;
          continue;
        }

        const detail = await fetchProductDetail(runtime, opts, code, productUrl);
        summary.productDetailsFetched++;

        const reviewPages = [];
        let remainingReviews = opts.reviewLimit;
        for (let reviewPage = 0; reviewPage < opts.reviewPageLimit; reviewPage++) {
          const rawReviewData = await fetchReviewPage(runtime, opts, code, productUrl, reviewPage);
          const reviewData = limitReviewPage(rawReviewData, remainingReviews);
          await writeJSON(
            join(dirs.reviews, `${code}-page-${String(reviewPage).padStart(4, "0")}.json`),
            reviewData
          );
          summary.reviewPagesFetched++;
          const count = reviewData.reviews?.length ?? 0;
          summary.reviewItemsAvailable += reviewData.availableReviewCount ?? count;
          summary.reviewItems += count;
          reviewPages.push(reviewData);
          if (Number.isFinite(remainingReviews)) remainingReviews -= count;
          if (count === 0) break;
          if (remainingReviews <= 0) break;
        }

        const normalized = normalizeProduct({
          detail,
          listing: listingProduct,
          category,
          reviews: reviewPages,
        });
        await writeJSON(productPath, normalized);

        if (summary.productDetailsFetched === 1) {
          console.log(
            "  sample product:",
            JSON.stringify({
              code: normalized.code,
              name: normalized.name,
              price: normalized.price,
              variants: normalized.variantCount,
              reviewsFetched: normalized.reviews.fetchedCount,
            })
          );
        }

        if (opts.delayMs > 0) await sleep(opts.delayMs);
      }
    }
  }

  const elapsedSeconds = ((Date.now() - started) / 1000).toFixed(1);
  await writeJSON(join(opts.out, "summary.json"), { ...summary, elapsedSeconds });
  console.log(
    `Done: ${summary.productDetailsFetched} products fetched, ` +
      `${summary.productDetailsSkipped} skipped, ${summary.reviewItems} review items ` +
      `-> ${opts.out}/ in ${elapsedSeconds}s`
  );
  return summary;
}

if (import.meta.main) {
  run(parseArgs(process.argv.slice(2))).catch((error) => {
    console.error(error.stack || error.message);
    process.exit(1);
  });
}

export {
  discoverCategories,
  fetchListing,
  fetchProductDetail,
  fetchReviewPage,
  flattenCategoryHierarchy,
  normalizeProduct,
  parseArgs,
  run,
};
