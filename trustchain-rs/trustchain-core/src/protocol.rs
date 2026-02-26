//! TrustChain protocol state machine — proposal/agreement two-phase interaction.
//!
//! Maps to Python's `protocol.py`. Handles creation and validation of proposals
//! and agreements, maintaining chain integrity.

use crate::blockstore::BlockStore;
use crate::error::{Result, TrustChainError};
use crate::halfblock::{create_half_block, verify_block, HalfBlock};
use crate::identity::Identity;
use crate::types::{BlockType, GENESIS_HASH};

/// The TrustChain protocol engine. Manages proposal/agreement lifecycle for one agent.
pub struct TrustChainProtocol<S: BlockStore> {
    identity: Identity,
    store: S,
}

impl<S: BlockStore> TrustChainProtocol<S> {
    /// Create a new protocol instance for the given identity and store.
    pub fn new(identity: Identity, store: S) -> Self {
        Self { identity, store }
    }

    /// Get this agent's public key hex.
    pub fn pubkey(&self) -> String {
        self.identity.pubkey_hex()
    }

    /// Get a reference to the identity.
    pub fn identity(&self) -> &Identity {
        &self.identity
    }

    /// Get a reference to the block store.
    pub fn store(&self) -> &S {
        &self.store
    }

    /// Get a mutable reference to the block store.
    pub fn store_mut(&mut self) -> &mut S {
        &mut self.store
    }

    // -----------------------------------------------------------------------
    // Phase 1: PROPOSAL
    // -----------------------------------------------------------------------

    /// Create a proposal half-block for a counterparty.
    ///
    /// This creates a new block in our chain with `link_sequence_number = 0`
    /// (unknown until the counterparty responds with an agreement).
    pub fn create_proposal(
        &mut self,
        counterparty_pubkey: &str,
        transaction: serde_json::Value,
        timestamp: Option<f64>,
    ) -> Result<HalfBlock> {
        let seq = self.store.get_latest_seq(&self.pubkey())? + 1;
        let prev_hash = self.store.get_head_hash(&self.pubkey())?;

        let block = create_half_block(
            &self.identity,
            seq,
            counterparty_pubkey,
            0, // link_sequence_number unknown for proposals
            &prev_hash,
            BlockType::Proposal,
            transaction,
            timestamp,
        );

        self.store.add_block(&block)?;
        Ok(block)
    }

    /// Receive and validate a proposal from another agent.
    ///
    /// Validates:
    /// 1. Block type is proposal
    /// 2. The proposal is addressed to us
    /// 3. Signature is valid
    /// 4. Sequence continuity (if we know the proposer's chain)
    pub fn receive_proposal(&mut self, proposal: &HalfBlock) -> Result<bool> {
        // Must be a proposal.
        if !proposal.is_proposal() {
            return Err(TrustChainError::proposal(
                &proposal.public_key,
                proposal.sequence_number,
                format!("expected proposal, got {}", proposal.block_type),
            ));
        }

        // Must be addressed to us.
        if proposal.link_public_key != self.pubkey() {
            return Err(TrustChainError::proposal(
                &proposal.public_key,
                proposal.sequence_number,
                "proposal not addressed to us",
            ));
        }

        // Verify signature.
        if !verify_block(proposal)? {
            return Err(TrustChainError::signature(
                &proposal.public_key,
                proposal.sequence_number,
                "invalid signature on proposal",
            ));
        }

        // Check sequence continuity if we know this peer.
        let known_latest = self.store.get_latest_seq(&proposal.public_key)?;
        if known_latest > 0 {
            // If we already have this exact sequence, check if it's the same block (idempotent).
            if proposal.sequence_number <= known_latest {
                if let Ok(Some(existing)) = self.store.get_block(&proposal.public_key, proposal.sequence_number) {
                    if existing.block_hash == proposal.block_hash {
                        return Ok(true); // Already stored, idempotent.
                    }
                }
                // Different block at same or earlier seq — reject.
                return Err(TrustChainError::sequence_gap(
                    &proposal.public_key,
                    known_latest + 1,
                    proposal.sequence_number,
                ));
            }

            let expected_seq = known_latest + 1;
            if proposal.sequence_number != expected_seq {
                return Err(TrustChainError::sequence_gap(
                    &proposal.public_key,
                    expected_seq,
                    proposal.sequence_number,
                ));
            }
            let expected_prev = self.store.get_head_hash(&proposal.public_key)?;
            if proposal.previous_hash != expected_prev {
                return Err(TrustChainError::prev_hash_mismatch(
                    &proposal.public_key,
                    proposal.sequence_number,
                    &expected_prev,
                    &proposal.previous_hash,
                ));
            }
        }

        // Store the proposal in our store (idempotent — ignore duplicates).
        match self.store.add_block(proposal) {
            Ok(()) => {}
            Err(TrustChainError::DuplicateSequence { .. }) => {} // Already stored.
            Err(e) => return Err(e),
        }
        Ok(true)
    }

    // -----------------------------------------------------------------------
    // Phase 2: AGREEMENT
    // -----------------------------------------------------------------------

    /// Create an agreement half-block in response to a proposal.
    ///
    /// The agreement block links back to the proposal via `link_public_key`
    /// and `link_sequence_number`, and copies the transaction payload.
    pub fn create_agreement(
        &mut self,
        proposal: &HalfBlock,
        timestamp: Option<f64>,
    ) -> Result<HalfBlock> {
        // Must be a proposal block.
        if !proposal.is_proposal() {
            return Err(TrustChainError::agreement(
                &proposal.public_key,
                proposal.sequence_number,
                format!("cannot agree to non-proposal block type: {}", proposal.block_type),
            ));
        }

        // Must be addressed to us.
        if proposal.link_public_key != self.pubkey() {
            return Err(TrustChainError::agreement(
                &proposal.public_key,
                proposal.sequence_number,
                "proposal is not addressed to us",
            ));
        }

        // Verify the proposal signature.
        if !verify_block(proposal)? {
            return Err(TrustChainError::proposal(
                &proposal.public_key,
                proposal.sequence_number,
                "cannot agree to invalid proposal",
            ));
        }

        let seq = self.store.get_latest_seq(&self.pubkey())? + 1;
        let prev_hash = self.store.get_head_hash(&self.pubkey())?;

        let block = create_half_block(
            &self.identity,
            seq,
            &proposal.public_key,
            proposal.sequence_number, // link back to the proposal
            &prev_hash,
            BlockType::Agreement,
            proposal.transaction.clone(), // copy transaction from proposal
            timestamp,
        );

        self.store.add_block(&block)?;
        Ok(block)
    }

    /// Receive and validate an agreement from another agent.
    ///
    /// Validates:
    /// 1. Block type is agreement
    /// 2. The agreement links to us
    /// 3. Signature is valid
    /// 4. The linked proposal exists in our store
    /// 5. Transaction data matches the original proposal
    pub fn receive_agreement(&mut self, agreement: &HalfBlock) -> Result<bool> {
        // Must be an agreement.
        if !agreement.is_agreement() {
            return Err(TrustChainError::agreement(
                &agreement.public_key,
                agreement.sequence_number,
                format!("expected agreement, got {}", agreement.block_type),
            ));
        }

        // Must link to us.
        if agreement.link_public_key != self.pubkey() {
            return Err(TrustChainError::agreement(
                &agreement.public_key,
                agreement.sequence_number,
                "agreement does not link to us",
            ));
        }

        // Verify signature.
        if !verify_block(agreement)? {
            return Err(TrustChainError::signature(
                &agreement.public_key,
                agreement.sequence_number,
                "invalid signature on agreement",
            ));
        }

        // The linked proposal must exist in our store.
        let proposal = self
            .store
            .get_block(&self.pubkey(), agreement.link_sequence_number)?
            .ok_or_else(|| {
                TrustChainError::agreement(
                    &agreement.public_key,
                    agreement.sequence_number,
                    format!(
                        "linked proposal not found: seq {}",
                        agreement.link_sequence_number
                    ),
                )
            })?;

        // Linked block must be a proposal.
        if !proposal.is_proposal() {
            return Err(TrustChainError::agreement(
                &agreement.public_key,
                agreement.sequence_number,
                format!(
                    "linked block is not a proposal: {}",
                    proposal.block_type
                ),
            ));
        }

        // Transaction must match.
        if agreement.transaction != proposal.transaction {
            return Err(TrustChainError::agreement(
                &agreement.public_key,
                agreement.sequence_number,
                "agreement transaction does not match proposal",
            ));
        }

        // Store the agreement (idempotent — ignore duplicates).
        match self.store.add_block(agreement) {
            Ok(()) => {}
            Err(TrustChainError::DuplicateSequence { .. }) => {} // Already stored.
            Err(e) => return Err(e),
        }
        Ok(true)
    }

    // -----------------------------------------------------------------------
    // Chain validation
    // -----------------------------------------------------------------------

    /// Validate an agent's entire chain: sequence continuity, hash links, signatures.
    pub fn validate_chain(&self, pubkey: &str) -> Result<bool> {
        let chain = self.store.get_chain(pubkey)?;

        for (i, block) in chain.iter().enumerate() {
            let expected_seq = (i as u64) + 1;
            if block.sequence_number != expected_seq {
                return Err(TrustChainError::sequence_gap(
                    pubkey,
                    expected_seq,
                    block.sequence_number,
                ));
            }

            let expected_prev = if i == 0 {
                GENESIS_HASH.to_string()
            } else {
                chain[i - 1].block_hash.clone()
            };
            if block.previous_hash != expected_prev {
                return Err(TrustChainError::prev_hash_mismatch(
                    pubkey,
                    block.sequence_number,
                    &expected_prev,
                    &block.previous_hash,
                ));
            }

            if !verify_block(block)? {
                return Err(TrustChainError::signature(
                    pubkey,
                    block.sequence_number,
                    "invalid signature",
                ));
            }
        }

        Ok(true)
    }

    /// Compute the integrity score for an agent's chain (0.0 to 1.0).
    ///
    /// Returns the fraction of blocks that are valid before the first error.
    /// An empty chain returns 1.0.
    pub fn integrity_score(&self, pubkey: &str) -> Result<f64> {
        let chain = self.store.get_chain(pubkey)?;
        if chain.is_empty() {
            return Ok(1.0);
        }

        let total = chain.len() as f64;
        for (i, block) in chain.iter().enumerate() {
            let expected_seq = (i as u64) + 1;
            if block.sequence_number != expected_seq {
                return Ok(i as f64 / total);
            }

            let expected_prev = if i == 0 {
                GENESIS_HASH.to_string()
            } else {
                chain[i - 1].block_hash.clone()
            };
            if block.previous_hash != expected_prev {
                return Ok(i as f64 / total);
            }

            if verify_block(block).unwrap_or(false) == false {
                return Ok(i as f64 / total);
            }
        }

        Ok(1.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::blockstore::MemoryBlockStore;

    fn make_protocol() -> (TrustChainProtocol<MemoryBlockStore>, TrustChainProtocol<MemoryBlockStore>) {
        let alice = Identity::from_bytes(&[1u8; 32]);
        let bob = Identity::from_bytes(&[2u8; 32]);
        let proto_a = TrustChainProtocol::new(alice, MemoryBlockStore::new());
        let proto_b = TrustChainProtocol::new(bob, MemoryBlockStore::new());
        (proto_a, proto_b)
    }

    #[test]
    fn test_create_proposal() {
        let (mut alice, _bob) = make_protocol();
        let bob_key = Identity::from_bytes(&[2u8; 32]).pubkey_hex();

        let proposal = alice
            .create_proposal(&bob_key, serde_json::json!({"service": "compute"}), Some(1000.0))
            .unwrap();

        assert_eq!(proposal.sequence_number, 1);
        assert_eq!(proposal.link_sequence_number, 0);
        assert_eq!(proposal.previous_hash, GENESIS_HASH);
        assert!(proposal.is_proposal());
        assert!(verify_block(&proposal).unwrap());
    }

    #[test]
    fn test_full_proposal_agreement_roundtrip() {
        let (mut alice, mut bob) = make_protocol();

        // Alice creates proposal for Bob.
        let proposal = alice
            .create_proposal(
                &bob.pubkey(),
                serde_json::json!({"service": "compute", "amount": 100}),
                Some(1000.0),
            )
            .unwrap();

        // Bob receives and validates the proposal.
        assert!(bob.receive_proposal(&proposal).unwrap());

        // Bob creates agreement.
        let agreement = bob.create_agreement(&proposal, Some(1001.0)).unwrap();
        assert!(agreement.is_agreement());
        assert_eq!(agreement.link_public_key, alice.pubkey());
        assert_eq!(agreement.link_sequence_number, proposal.sequence_number);
        assert_eq!(agreement.transaction, proposal.transaction);

        // Alice receives the agreement.
        assert!(alice.receive_agreement(&agreement).unwrap());

        // Both chains are valid.
        assert!(alice.validate_chain(&alice.pubkey()).unwrap());
        assert!(bob.validate_chain(&bob.pubkey()).unwrap());
    }

    #[test]
    fn test_multiple_interactions() {
        let (mut alice, mut bob) = make_protocol();

        for i in 0..5 {
            let proposal = alice
                .create_proposal(
                    &bob.pubkey(),
                    serde_json::json!({"round": i}),
                    Some(1000.0 + i as f64 * 2.0),
                )
                .unwrap();

            bob.receive_proposal(&proposal).unwrap();
            let agreement = bob.create_agreement(&proposal, Some(1001.0 + i as f64 * 2.0)).unwrap();
            alice.receive_agreement(&agreement).unwrap();
        }

        // Alice has 5 proposals.
        assert_eq!(alice.store().get_latest_seq(&alice.pubkey()).unwrap(), 5);
        // Bob has 5 agreements.
        assert_eq!(bob.store().get_latest_seq(&bob.pubkey()).unwrap(), 5);

        // Both chains are valid.
        assert!(alice.validate_chain(&alice.pubkey()).unwrap());
        assert!(bob.validate_chain(&bob.pubkey()).unwrap());
    }

    #[test]
    fn test_proposal_wrong_recipient() {
        let (mut alice, mut bob) = make_protocol();
        let charlie_key = Identity::from_bytes(&[3u8; 32]).pubkey_hex();

        // Alice proposes to Charlie, not Bob.
        let proposal = alice
            .create_proposal(&charlie_key, serde_json::json!({}), Some(1000.0))
            .unwrap();

        // Bob should reject it.
        let result = bob.receive_proposal(&proposal);
        assert!(result.is_err());
    }

    #[test]
    fn test_agreement_wrong_recipient() {
        let (mut alice, mut bob) = make_protocol();
        let mut charlie = TrustChainProtocol::new(
            Identity::from_bytes(&[3u8; 32]),
            MemoryBlockStore::new(),
        );

        let proposal = alice
            .create_proposal(&bob.pubkey(), serde_json::json!({}), Some(1000.0))
            .unwrap();

        bob.receive_proposal(&proposal).unwrap();
        let agreement = bob.create_agreement(&proposal, Some(1001.0)).unwrap();

        // Charlie should reject the agreement (it links to Alice, not Charlie).
        let result = charlie.receive_agreement(&agreement);
        assert!(result.is_err());
    }

    #[test]
    fn test_agreement_missing_proposal() {
        let (mut alice, mut bob) = make_protocol();

        let proposal = alice
            .create_proposal(&bob.pubkey(), serde_json::json!({}), Some(1000.0))
            .unwrap();

        // Bob creates agreement without first receiving the proposal.
        bob.receive_proposal(&proposal).unwrap();
        let agreement = bob.create_agreement(&proposal, Some(1001.0)).unwrap();

        // Create a fresh Alice that doesn't have the proposal in store.
        let mut alice2 = TrustChainProtocol::new(
            Identity::from_bytes(&[1u8; 32]),
            MemoryBlockStore::new(),
        );
        let result = alice2.receive_agreement(&agreement);
        assert!(result.is_err());
    }

    #[test]
    fn test_agreement_transaction_mismatch() {
        let (mut alice, mut bob) = make_protocol();

        let proposal = alice
            .create_proposal(
                &bob.pubkey(),
                serde_json::json!({"service": "compute"}),
                Some(1000.0),
            )
            .unwrap();

        bob.receive_proposal(&proposal).unwrap();

        // Create a tampered agreement with different transaction.
        let mut tampered_proposal = proposal.clone();
        tampered_proposal.transaction = serde_json::json!({"service": "FAKE"});
        // Bob would create agreement with wrong transaction — but create_agreement
        // copies from proposal, so we'd need to tamper post-creation.
        let agreement = bob.create_agreement(&proposal, Some(1001.0)).unwrap();

        // Tamper the agreement after creation.
        let mut tampered_agreement = agreement.clone();
        tampered_agreement.transaction = serde_json::json!({"service": "FAKE"});
        // Re-hash and re-sign won't work because Bob would need to sign the tampered version.
        // But the hash will mismatch, so verification will fail.
        let result = alice.receive_agreement(&tampered_agreement);
        assert!(result.is_err());
    }

    #[test]
    fn test_integrity_score_perfect() {
        let (mut alice, mut bob) = make_protocol();

        for i in 0..3 {
            let proposal = alice
                .create_proposal(&bob.pubkey(), serde_json::json!({"i": i}), Some(1000.0 + i as f64))
                .unwrap();
            bob.receive_proposal(&proposal).unwrap();
            let agreement = bob.create_agreement(&proposal, Some(1001.0 + i as f64)).unwrap();
            alice.receive_agreement(&agreement).unwrap();
        }

        assert_eq!(alice.integrity_score(&alice.pubkey()).unwrap(), 1.0);
    }

    #[test]
    fn test_integrity_score_empty_chain() {
        let (alice, _) = make_protocol();
        assert_eq!(alice.integrity_score(&alice.pubkey()).unwrap(), 1.0);
    }

    #[test]
    fn test_chain_validation() {
        let (mut alice, mut bob) = make_protocol();

        let proposal = alice
            .create_proposal(&bob.pubkey(), serde_json::json!({}), Some(1000.0))
            .unwrap();
        bob.receive_proposal(&proposal).unwrap();
        let agreement = bob.create_agreement(&proposal, Some(1001.0)).unwrap();
        alice.receive_agreement(&agreement).unwrap();

        assert!(alice.validate_chain(&alice.pubkey()).unwrap());
        assert!(bob.validate_chain(&bob.pubkey()).unwrap());
    }

    #[test]
    fn test_sequence_numbers_increment() {
        let (mut alice, mut bob) = make_protocol();

        for i in 1..=3 {
            let proposal = alice
                .create_proposal(&bob.pubkey(), serde_json::json!({"i": i}), Some(1000.0 + i as f64))
                .unwrap();
            assert_eq!(proposal.sequence_number, i);

            bob.receive_proposal(&proposal).unwrap();
            let agreement = bob.create_agreement(&proposal, Some(1001.0 + i as f64)).unwrap();
            assert_eq!(agreement.sequence_number, i);

            alice.receive_agreement(&agreement).unwrap();
        }
    }

    #[test]
    fn test_previous_hash_chain() {
        let (mut alice, mut bob) = make_protocol();

        let p1 = alice
            .create_proposal(&bob.pubkey(), serde_json::json!({"i": 1}), Some(1000.0))
            .unwrap();
        assert_eq!(p1.previous_hash, GENESIS_HASH);

        bob.receive_proposal(&p1).unwrap();
        let a1 = bob.create_agreement(&p1, Some(1001.0)).unwrap();
        alice.receive_agreement(&a1).unwrap();

        let p2 = alice
            .create_proposal(&bob.pubkey(), serde_json::json!({"i": 2}), Some(1002.0))
            .unwrap();
        assert_eq!(p2.previous_hash, p1.block_hash);
    }
}
