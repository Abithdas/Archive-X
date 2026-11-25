//! The local assistant, ported from `telegram_archive/ai`: the rules for what it may see and do, its notebook of findings, and what it knows
//! about Archive X itself.

pub mod findings;
pub mod knowledge;
pub mod persistence;
mod pytext;
pub mod safety;
mod settings;
