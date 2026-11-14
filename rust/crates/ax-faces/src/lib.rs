//! Learning from what a person has confirmed and removed.
//!
//! The Python side groups faces and lets someone name a group (a *confirmed* face) or take a face out of it (a *removed*, "not this person"
//! face). Until now none of that taught the system anything. This crate turns it into three things:
//!
//! * a **profile** for each named person: their confirmed faces with the odd ones left out, summarised as a centre point and up to
//!   [`EXEMPLARS`] varied example faces (so one profile picture is no longer the only evidence);
//! * **outliers**: which of a person's confirmed faces look least like the rest (the likely mistakes to check first);
//! * **suggestions**: faces still in unnamed groups that look clearly like a named person, are not closer to a face that was removed from
//!   that person, and do not look more like somebody else.
//!
//! Pure computation on face vectors (SFace gives 128 numbers per face); reading the database is the caller's job. Nothing here changes
//! anything: the person confirms every suggestion.

use std::collections::{HashMap, HashSet};

pub const DIM: usize = 128;
/// How many varied example faces describe one person.
pub const EXEMPLARS: usize = 24;
/// A face must score at least this against a person (mean of its three best matches among the examples) to be suggested. SFace's own
/// "same person" score is about 0.36; the existing grouping uses 0.42 to cluster and 0.46 to name from a profile picture.
pub const SUGGEST_MIN: f32 = 0.46;
/// From here a suggestion is called strong.
pub const STRONG_MIN: f32 = 0.56;
/// A suggestion must beat the closest "not this person" face by this much.
pub const NEGATIVE_MARGIN: f32 = 0.04;
/// Another named person whose centre is this much closer than this person's centre wins the face.
pub const RIVAL_MARGIN: f32 = 0.03;
/// Bounds for the "looks different" line, which is otherwise taken from how spread out the person's own faces are.
pub const DIFFERENT_FLOOR: f32 = 0.25;
pub const DIFFERENT_CEIL: f32 = 0.45;
/// The spread of a person's scores is never taken as smaller than this when trimming, so an unusually tidy set of faces does not make an
/// ordinary different look (a turned head, other lighting) count as a mistake.
const MIN_SPREAD: f32 = 0.05;
const TOP: usize = 3;

/// A face vector scaled to length one, or `None` when it is unusable (wrong size, zero, not finite).
pub fn normalize(v: &[f32]) -> Option<Vec<f32>> {
    if v.len() != DIM || v.iter().any(|x| !x.is_finite()) {
        return None;
    }
    let n = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    if n == 0.0 {
        return None;
    }
    Some(v.iter().map(|x| x / n).collect())
}

/// The JSON array of 128 numbers the database stores, normalised.
pub fn decode(json: &str) -> Option<Vec<f32>> {
    let v: serde_json::Value = serde_json::from_str(json).ok()?;
    let nums: Vec<f32> = v.as_array()?.iter().filter_map(|x| x.as_f64()).map(|x| x as f32).collect();
    normalize(&nums)
}

fn dot(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b).map(|(x, y)| x * y).sum()
}

fn median(values: &mut [f32]) -> f32 {
    if values.is_empty() {
        return 0.0;
    }
    values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let n = values.len();
    if n % 2 == 1 { values[n / 2] } else { (values[n / 2 - 1] + values[n / 2]) / 2.0 }
}

/// Median and the usual robust spread (1.4826 × median absolute deviation).
fn robust_spread(values: &[f32]) -> (f32, f32) {
    let mut v = values.to_vec();
    let m = median(&mut v);
    let mut dev: Vec<f32> = values.iter().map(|x| (x - m).abs()).collect();
    (m, 1.4826 * median(&mut dev))
}

fn centroid(vectors: &[&[f32]]) -> Option<Vec<f32>> {
    let mut sum = vec![0.0f32; DIM];
    for v in vectors {
        for (s, x) in sum.iter_mut().zip(v.iter()) {
            *s += x;
        }
    }
    normalize(&sum)
}

/// One detected face.
#[derive(Debug, Clone)]
pub struct Detection {
    pub id: i64,
    pub photo_id: i64,
    pub group_id: i64,
    /// The person its group is named after, if any.
    pub performer: Option<i64>,
    /// Normalised (see [`normalize`]).
    pub v: Vec<f32>,
}

/// A face with how well it fits.
#[derive(Debug, Clone, PartialEq)]
pub struct Scored {
    pub detection_id: i64,
    pub photo_id: i64,
    pub group_id: i64,
    pub score: f32,
    /// Outliers: looks different from the rest. Suggestions: a strong match.
    pub flag: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Counts {
    pub performer: i64,
    pub confirmed: usize,
    pub removed: usize,
}

/// What is known about one person.
pub struct Profile {
    /// Confirmed faces that were kept (the odd ones left out).
    pub kept: usize,
    pub exemplars: Vec<Vec<f32>>,
    pub centre: Vec<f32>,
    negatives: Vec<Vec<f32>>,
}

impl Profile {
    /// Mean of the best `TOP` matches among the examples (skipping the example with id `skip`, so a face is not scored against itself).
    fn score(&self, v: &[f32], skip: Option<usize>) -> f32 {
        let mut sims: Vec<f32> = self.exemplars.iter().enumerate().filter(|(i, _)| Some(*i) != skip).map(|(_, e)| dot(v, e)).collect();
        if sims.is_empty() {
            return -1.0;
        }
        sims.sort_by(|a, b| b.partial_cmp(a).unwrap_or(std::cmp::Ordering::Equal));
        let n = sims.len().min(TOP);
        sims[..n].iter().sum::<f32>() / n as f32
    }

    fn closest_negative(&self, v: &[f32]) -> f32 {
        self.negatives.iter().map(|n| dot(v, n)).fold(-1.0, f32::max)
    }
}

pub struct Learner {
    dets: Vec<Detection>,
    /// performer -> detections that were taken out of that person
    negatives: HashMap<i64, HashSet<i64>>,
    by_id: HashMap<i64, usize>,
}

impl Learner {
    pub fn new(dets: Vec<Detection>, negatives: impl IntoIterator<Item = (i64, i64)>) -> Learner {
        let mut neg: HashMap<i64, HashSet<i64>> = HashMap::new();
        for (detection, performer) in negatives {
            neg.entry(performer).or_default().insert(detection);
        }
        let by_id = dets.iter().enumerate().map(|(i, d)| (d.id, i)).collect();
        Learner { dets, negatives: neg, by_id }
    }

    pub fn len(&self) -> usize {
        self.dets.len()
    }

    pub fn is_empty(&self) -> bool {
        self.dets.is_empty()
    }

    /// Confirmed and removed faces for everybody who has either.
    pub fn counts(&self) -> Vec<Counts> {
        let mut confirmed: HashMap<i64, usize> = HashMap::new();
        for d in &self.dets {
            if let Some(p) = d.performer {
                *confirmed.entry(p).or_default() += 1;
            }
        }
        let mut people: HashSet<i64> = confirmed.keys().copied().collect();
        people.extend(self.negatives.keys().copied());
        let mut out: Vec<Counts> = people
            .into_iter()
            .map(|p| Counts { performer: p, confirmed: confirmed.get(&p).copied().unwrap_or(0), removed: self.negatives.get(&p).map(|s| s.iter().filter(|id| self.by_id.contains_key(id)).count()).unwrap_or(0) })
            .collect();
        out.sort_by_key(|c| (std::cmp::Reverse(c.confirmed + c.removed), c.performer));
        out
    }

    fn confirmed_of(&self, performer: i64) -> Vec<&Detection> {
        let neg = self.negatives.get(&performer);
        self.dets.iter().filter(|d| d.performer == Some(performer) && !neg.is_some_and(|n| n.contains(&d.id))).collect()
    }

    /// The person's profile: confirmed faces with the odd ones trimmed away (twice, so a few wrong faces cannot drag the centre off),
    /// then up to [`EXEMPLARS`] varied examples. `None` when the person has no usable confirmed face.
    pub fn profile(&self, performer: i64) -> Option<Profile> {
        let all = self.confirmed_of(performer);
        if all.is_empty() {
            return None;
        }
        let mut kept: Vec<&Detection> = all.clone();
        if kept.len() >= 5 {
            for _ in 0..2 {
                let refs: Vec<&[f32]> = kept.iter().map(|d| d.v.as_slice()).collect();
                let Some(c) = centroid(&refs) else { break };
                let scores: Vec<f32> = kept.iter().map(|d| dot(&d.v, &c)).collect();
                let (m, spread) = robust_spread(&scores);
                let cut = m - 2.5 * spread.max(MIN_SPREAD);
                let next: Vec<&Detection> = kept.iter().zip(&scores).filter(|(_, s)| **s >= cut).map(|(d, _)| *d).collect();
                if next.len() < 3 || next.len() == kept.len() {
                    break;
                }
                kept = next;
            }
        }
        let refs: Vec<&[f32]> = kept.iter().map(|d| d.v.as_slice()).collect();
        let centre = centroid(&refs)?;
        let exemplars = pick_varied(&kept, &centre, EXEMPLARS);
        let negatives = self
            .negatives
            .get(&performer)
            .map(|ids| ids.iter().filter_map(|id| self.by_id.get(id)).map(|i| self.dets[*i].v.clone()).collect())
            .unwrap_or_default();
        Some(Profile { kept: kept.len(), exemplars, centre, negatives })
    }

    /// Every confirmed face of the person, least like the rest first. `flag` marks the ones that look different. `threshold` is the line used.
    pub fn outliers(&self, performer: i64) -> Outliers {
        let all = self.confirmed_of(performer);
        let Some(profile) = self.profile(performer) else { return Outliers { items: vec![], threshold: DIFFERENT_FLOOR } };
        let exemplar_of: HashMap<usize, usize> = profile
            .exemplars
            .iter()
            .enumerate()
            .filter_map(|(i, e)| all.iter().position(|d| d.v == *e).map(|p| (p, i)))
            .collect();
        let mut items: Vec<Scored> = all
            .iter()
            .enumerate()
            .map(|(pos, d)| Scored { detection_id: d.id, photo_id: d.photo_id, group_id: d.group_id, score: profile.score(&d.v, exemplar_of.get(&pos).copied()), flag: false })
            .collect();
        let scores: Vec<f32> = items.iter().map(|i| i.score).collect();
        let (m, spread) = robust_spread(&scores);
        let threshold = (m - 2.5 * spread).clamp(DIFFERENT_FLOOR, DIFFERENT_CEIL);
        for i in &mut items {
            i.flag = i.score < threshold;
        }
        items.sort_by(|a, b| a.score.partial_cmp(&b.score).unwrap_or(std::cmp::Ordering::Equal).then(a.detection_id.cmp(&b.detection_id)));
        Outliers { items, threshold }
    }

    /// Faces in unnamed groups that look like this person, best first.
    pub fn suggestions(&self, performer: i64) -> Vec<Scored> {
        let Some(profile) = self.profile(performer) else { return vec![] };
        let neg = self.negatives.get(&performer);
        // Everybody else's centre, to tell when a face looks more like somebody else.
        let rivals: Vec<(i64, Vec<f32>)> = self.people().into_iter().filter(|p| *p != performer).filter_map(|p| self.profile(p).map(|pr| (p, pr.centre))).collect();
        let mut out = Vec::new();
        for d in &self.dets {
            if d.performer.is_some() || neg.is_some_and(|n| n.contains(&d.id)) {
                continue;
            }
            let score = profile.score(&d.v, None);
            if score < SUGGEST_MIN {
                continue;
            }
            if !profile.negatives.is_empty() && score - profile.closest_negative(&d.v) < NEGATIVE_MARGIN {
                continue;
            }
            let mine = dot(&d.v, &profile.centre);
            if rivals.iter().any(|(_, c)| dot(&d.v, c) > mine + RIVAL_MARGIN) {
                continue;
            }
            out.push(Scored { detection_id: d.id, photo_id: d.photo_id, group_id: d.group_id, score, flag: score >= STRONG_MIN });
        }
        out.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal).then(a.detection_id.cmp(&b.detection_id)));
        out
    }

    fn people(&self) -> Vec<i64> {
        let mut s: Vec<i64> = self.dets.iter().filter_map(|d| d.performer).collect::<HashSet<_>>().into_iter().collect();
        s.sort_unstable();
        s
    }
}

pub struct Outliers {
    pub items: Vec<Scored>,
    pub threshold: f32,
}

/// Up to `k` faces that cover the person's range of looks: start from the one nearest the centre, then keep adding the face least like
/// any already chosen.
fn pick_varied(kept: &[&Detection], centre: &[f32], k: usize) -> Vec<Vec<f32>> {
    if kept.len() <= k {
        return kept.iter().map(|d| d.v.clone()).collect();
    }
    let first = kept.iter().enumerate().max_by(|a, b| dot(&a.1.v, centre).partial_cmp(&dot(&b.1.v, centre)).unwrap_or(std::cmp::Ordering::Equal)).map(|(i, _)| i).unwrap_or(0);
    let mut chosen = vec![first];
    let mut best: Vec<f32> = kept.iter().map(|d| dot(&d.v, &kept[first].v)).collect();
    while chosen.len() < k {
        let next = best.iter().enumerate().filter(|(i, _)| !chosen.contains(i)).min_by(|a, b| a.1.partial_cmp(b.1).unwrap_or(std::cmp::Ordering::Equal)).map(|(i, _)| i);
        let Some(next) = next else { break };
        chosen.push(next);
        for (i, b) in best.iter_mut().enumerate() {
            *b = b.max(dot(&kept[i].v, &kept[next].v));
        }
    }
    chosen.into_iter().map(|i| kept[i].v.clone()).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A tiny deterministic generator, so the synthetic faces are the same on every run.
    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> f32 {
            self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            ((self.0 >> 33) as f32 / (1u64 << 31) as f32) * 2.0 - 1.0
        }
        fn unit(&mut self) -> Vec<f32> {
            let v: Vec<f32> = (0..DIM).map(|_| self.next()).collect();
            normalize(&v).unwrap()
        }
        /// A face of the person whose "true" vector is `base`: the same direction plus noise of the given size.
        fn face(&mut self, base: &[f32], noise: f32) -> Vec<f32> {
            let v: Vec<f32> = base.iter().map(|b| b + noise * self.next()).collect();
            normalize(&v).unwrap()
        }
    }

    struct World {
        dets: Vec<Detection>,
        next_id: i64,
    }

    impl World {
        fn new() -> World {
            World { dets: vec![], next_id: 1 }
        }
        fn add(&mut self, v: Vec<f32>, performer: Option<i64>, group: i64) -> i64 {
            let id = self.next_id;
            self.next_id += 1;
            self.dets.push(Detection { id, photo_id: id * 10, group_id: group, performer, v });
            id
        }
    }

    fn ids(items: &[Scored]) -> Vec<i64> {
        items.iter().map(|s| s.detection_id).collect()
    }

    #[test]
    fn decode_reads_the_stored_json_and_rejects_bad_vectors() {
        let json = format!("[{}]", (0..DIM).map(|i| format!("{}", i as f32 + 1.0)).collect::<Vec<_>>().join(","));
        let v = decode(&json).unwrap();
        assert!((v.iter().map(|x| x * x).sum::<f32>() - 1.0).abs() < 1e-4);
        assert!(decode("[1,2,3]").is_none() && decode("nope").is_none());
        assert!(normalize(&vec![0.0; DIM]).is_none());
        let mut nan = vec![1.0; DIM];
        nan[3] = f32::NAN;
        assert!(normalize(&nan).is_none());
    }

    #[test]
    fn faces_that_do_not_belong_are_ranked_first_even_when_they_were_confirmed() {
        let mut r = Rng(1);
        let (ava, other) = (r.unit(), r.unit());
        let mut w = World::new();
        let mut good = vec![];
        for _ in 0..40 {
            let v = r.face(&ava, 0.05);
            good.push(w.add(v, Some(7), 1));
        }
        let wrong: Vec<i64> = (0..4).map(|_| { let v = r.face(&other, 0.05); w.add(v, Some(7), 1) }).collect();
        let l = Learner::new(w.dets, []);
        let o = l.outliers(7);
        assert_eq!(o.items.len(), 44);
        let first4: HashSet<i64> = ids(&o.items[..4]).into_iter().collect();
        assert_eq!(first4, wrong.iter().copied().collect(), "the four planted mistakes come first");
        assert!(o.items[..4].iter().all(|s| s.flag), "and are flagged");
        assert!(o.items[4..].iter().all(|s| !s.flag), "none of the 40 real faces is flagged");
        assert!((DIFFERENT_FLOOR..=DIFFERENT_CEIL).contains(&o.threshold));
    }

    #[test]
    fn a_few_wrong_faces_do_not_pull_the_profile_off() {
        let mut r = Rng(2);
        let (ava, other) = (r.unit(), r.unit());
        let mut w = World::new();
        for _ in 0..30 {
            let v = r.face(&ava, 0.05);
            w.add(v, Some(1), 1);
        }
        for _ in 0..6 {
            let v = r.face(&other, 0.05);
            w.add(v, Some(1), 1);
        }
        let l = Learner::new(w.dets, []);
        let p = l.profile(1).unwrap();
        assert_eq!(p.kept, 30, "the six wrong faces are trimmed away");
        assert!(dot(&p.centre, &ava) > 0.95, "the centre sits on the real person: {}", dot(&p.centre, &ava));
    }

    #[test]
    fn unnamed_faces_that_look_like_a_named_person_are_suggested_and_others_are_not() {
        let mut r = Rng(3);
        let (ava, mia, stranger) = (r.unit(), r.unit(), r.unit());
        let mut w = World::new();
        for _ in 0..20 {
            let v = r.face(&ava, 0.05);
            w.add(v, Some(1), 1);
        }
        for _ in 0..20 {
            let v = r.face(&mia, 0.05);
            w.add(v, Some(2), 2);
        }
        let ava_unnamed: Vec<i64> = (0..6).map(|_| { let v = r.face(&ava, 0.05); w.add(v, None, 9) }).collect();
        let mia_unnamed: Vec<i64> = (0..3).map(|_| { let v = r.face(&mia, 0.05); w.add(v, None, 10) }).collect();
        for _ in 0..5 {
            let v = r.face(&stranger, 0.05);
            w.add(v, None, 11);
        }
        let l = Learner::new(w.dets, []);
        let s = l.suggestions(1);
        assert_eq!(s.iter().map(|x| x.detection_id).collect::<HashSet<_>>(), ava_unnamed.iter().copied().collect(), "only Ava's unnamed faces");
        assert!(s.iter().all(|x| x.flag), "they match strongly");
        assert!(ids(&l.suggestions(2)).iter().all(|i| mia_unnamed.contains(i)) && l.suggestions(2).len() == 3);
        assert!(l.suggestions(99).is_empty(), "nobody by that number");
    }

    #[test]
    fn a_face_removed_from_a_person_is_never_suggested_back_and_neither_are_its_lookalikes() {
        let mut r = Rng(4);
        let ava = r.unit();
        // somebody who looks quite like Ava: close enough to score above the suggestion line against her
        let lookalike: Vec<f32> = { let n = r.unit(); let v: Vec<f32> = ava.iter().zip(&n).map(|(a, b)| a + 0.55 * b).collect(); normalize(&v).unwrap() };
        let mut w = World::new();
        for _ in 0..20 {
            let v = r.face(&ava, 0.05);
            w.add(v, Some(1), 1);
        }
        let removed = w.add(r.face(&lookalike, 0.03), None, 5);
        let twin = w.add(r.face(&lookalike, 0.03), None, 6);
        let real = w.add(r.face(&ava, 0.05), None, 7);
        let without = Learner::new(w.dets.clone(), []);
        let before = ids(&without.suggestions(1));
        assert!(before.contains(&removed) && before.contains(&twin) && before.contains(&real), "without the memory the lookalikes are suggested: {before:?}");
        let with = Learner::new(w.dets, [(removed, 1)]);
        let after = ids(&with.suggestions(1));
        assert!(!after.contains(&removed), "the removed face is not offered again");
        assert!(!after.contains(&twin), "nor its lookalike");
        assert!(after.contains(&real), "the real faces still are");
    }

    #[test]
    fn a_face_that_looks_more_like_another_named_person_goes_to_that_person() {
        let mut r = Rng(5);
        let ava = r.unit();
        let mia: Vec<f32> = { let n = r.unit(); let v: Vec<f32> = ava.iter().zip(&n).map(|(a, b)| a + 0.5 * b).collect(); normalize(&v).unwrap() };
        let mut w = World::new();
        for _ in 0..20 {
            let v = r.face(&ava, 0.04);
            w.add(v, Some(1), 1);
        }
        for _ in 0..20 {
            let v = r.face(&mia, 0.04);
            w.add(v, Some(2), 2);
        }
        let hers = w.add(r.face(&mia, 0.04), None, 3);
        let l = Learner::new(w.dets, []);
        assert!(!ids(&l.suggestions(1)).contains(&hers), "not offered to Ava");
        assert!(ids(&l.suggestions(2)).contains(&hers), "offered to Mia");
    }

    #[test]
    fn counts_list_confirmed_and_removed_faces_per_person() {
        let mut r = Rng(6);
        let v = r.unit();
        let mut w = World::new();
        let a = w.add(v.clone(), Some(1), 1);
        w.add(v.clone(), Some(1), 1);
        let gone = w.add(v.clone(), None, 2);
        let l = Learner::new(w.dets, [(gone, 1), (a, 2), (9999, 1)]);
        let c = l.counts();
        let of = |p: i64| c.iter().find(|x| x.performer == p).map(|x| (x.confirmed, x.removed));
        assert_eq!(of(1), Some((2, 1)), "two confirmed faces, one removed (a removed detection that no longer exists is not counted)");
        assert_eq!(of(2), Some((0, 1)));
    }

    #[test]
    fn small_and_empty_cases_do_not_fail() {
        let l = Learner::new(vec![], []);
        assert!(l.is_empty() && l.counts().is_empty() && l.suggestions(1).is_empty() && l.outliers(1).items.is_empty() && l.profile(1).is_none());
        let mut r = Rng(7);
        let mut w = World::new();
        w.add(r.unit(), Some(1), 1);
        w.add(r.unit(), Some(1), 1);
        let l = Learner::new(w.dets, []);
        assert_eq!(l.outliers(1).items.len(), 2);
        assert!(l.outliers(1).items.iter().all(|i| !i.flag || i.score < DIFFERENT_CEIL));
    }

    #[test]
    fn examples_cover_the_range_of_looks_not_just_the_commonest() {
        let mut r = Rng(8);
        let (front, side) = (r.unit(), r.unit());
        let mut w = World::new();
        for _ in 0..60 {
            let v = r.face(&front, 0.03);
            w.add(v, Some(1), 1);
        }
        let mut sides = 0;
        for _ in 0..4 {
            let base: Vec<f32> = front.iter().zip(&side).map(|(a, b)| a + 0.5 * b).collect();
            let v = r.face(&normalize(&base).unwrap(), 0.03);
            w.add(v, Some(1), 1);
            sides += 1;
        }
        let l = Learner::new(w.dets, []);
        let p = l.profile(1).unwrap();
        assert_eq!(p.exemplars.len(), EXEMPLARS);
        let side_dir: Vec<f32> = normalize(&front.iter().zip(&side).map(|(a, b)| a + 0.5 * b).collect::<Vec<_>>()).unwrap();
        assert!(p.exemplars.iter().filter(|e| dot(e, &side_dir) > dot(e, &front)).count() >= 1, "at least one example from the rarer look ({sides} such faces)");
    }
}
