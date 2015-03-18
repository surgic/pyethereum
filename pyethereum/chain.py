import os
import utils
import rlp
import blocks
import processblock
from pyethereum.slogging import get_logger
log = get_logger('eth.chain')


class Index(object):

    """"
    Collection of indexes

    children:
        - needed to get the uncles of a block
    blocknumbers:
        - needed to mark the longest chain (path to top)
    transactions:
        - optional to resolve txhash to block:tx

    """

    def __init__(self, db, index_transactions=True):
        self.db = db
        self._index_transactions = index_transactions

    def add_block(self, blk):
        self.add_child(blk.prevhash, blk.hash)
        if self._index_transactions:
            self._add_transactions(blk)

    # block by number #########
    def _block_by_number_key(self, number):
        return 'blocknumber:%d' % number

    def update_blocknumbers(self, blk):
        "start from head and update until the existing indices match the block"
        while True:
            self.db.put(self._block_by_number_key(blk.number), blk.hash)
            if blk.number == 0:
                break
            blk = blk.get_parent()
            if self.has_block_by_number(blk.number) and \
                    self.get_block_by_number(blk.number) == blk.hash:
                break

    def has_block_by_number(self, number):
        return self._block_by_number_key(number) in self.db

    def get_block_by_number(self, number):
        "returns block hash"
        return self.db.get(self._block_by_number_key(number))

    # transactions #############
    def _add_transactions(self, blk):
        "'tx_hash' -> 'rlp([blockhash,tx_number])"
        for i, tx in enumerate(blk.get_transactions()):
            self.db.put(tx.hash, rlp.encode([blk.hash, i]))

    def get_transaction(self, txhash):
        "return (tx, block, index)"
        blockhash, tx_num_enc = rlp.decode(self.db.get(txhash))
        blk = rlp.decode(self.db.get(blockhash), blocks.Block, db=self.db)
        num = utils.decode_int(tx_num_enc)
        tx_data = blk.get_transaction(num)
        return tx_data, blk, num

    # children ##############

    def _child_db_key(self, blk_hash):
        return 'ci:' + blk_hash

    def add_child(self, parent_hash, child_hash):
        # only efficient for few children per block
        children = list(set(self.get_children(parent_hash) + [child_hash]))
        assert children.count(child_hash) == 1
        self.db.put(self._child_db_key(parent_hash), rlp.encode(children))

    def get_children(self, blk_hash):
        "returns block hashes"
        key = self._child_db_key(blk_hash)
        if key in self.db:
            return rlp.decode(self.db.get(key))
        return []


class Chain(object):

    """
    Manages the chain and requests to it.
    """

    def __init__(self, db, genesis=None, new_head_cb=None):
        self.blockchain = db
        self.new_head_cb = new_head_cb
        self.index = Index(db)
        if genesis:
            self._initialize_blockchain(genesis)
        log.debug('chain @', head_hash=self.head)
        self.genesis = blocks.genesis(db=db)
        log.debug('got genesis', genesis_hash=self.genesis)

    def _initialize_blockchain(self, genesis=None):
        log.info('Initializing new chain')
        if not genesis:
            genesis = blocks.genesis(self.blockchain)
            log.info('new genesis', genesis_hash=genesis)
            self.index.add_block(genesis)
        self._store_block(genesis)
        assert genesis == blocks.get_block(self.blockchain, genesis.hash)
        self._update_head(genesis)
        assert genesis.hash in self

    @property
    def head(self):
        if not self.blockchain or 'HEAD' not in self.blockchain:
            self._initialize_blockchain()
        ptr = self.blockchain.get('HEAD')
        return blocks.get_block(self.blockchain, ptr)

    def _update_head(self, block):
        if not block.is_genesis():
            assert self.head.chain_difficulty() < block.chain_difficulty()
            if block.get_parent() != self.head:
                log.debug('New Head is on a different branch',
                          head_hash=block, old_head_hash=self.head)
        self.blockchain.put('HEAD', block.hash)
        self.index.update_blocknumbers(self.head)
        if self.new_head_cb and not block.is_genesis():
            self.new_head_cb(block)

    def get(self, blockhash):
        assert isinstance(blockhash, str)
        assert len(blockhash) == 32
        return blocks.get_block(self.blockchain, blockhash)

    def has_block(self, blockhash):
        assert isinstance(blockhash, str)
        assert len(blockhash) == 32
        return blockhash in self.blockchain

    def __contains__(self, blockhash):
        return self.has_block(blockhash)

    def _store_block(self, block):
        self.blockchain.put(block.hash, rlp.encode(block))

    def commit(self):
        self.blockchain.commit()

    def add_block(self, block, forward=False):
        "returns True if block was added sucessfully"
        _log = log.bind(block_hash=block)
        # make sure we know the parent
        if not block.has_parent() and not block.is_genesis():
            _log.debug('missing parent')
            return False

        if not block.validate_uncles():
            _log.debug('invalid uncles')
            return False

        # check PoW and forward asap in order to avoid stale blocks
        if not len(block.nonce) == 8:
            _log.debug('nonce not set')
            raise Exception("qwrqwr")
            return False
        elif not block.header.check_pow(nonce=block.nonce) and\
                not block.is_genesis():
            _log.debug('invalid nonce')
            return False

        if block.has_parent():
            try:
                processblock.verify(block, block.get_parent())
            except processblock.VerificationFailed as e:
                _log.critical('VERIFICATION FAILED', error=e)
                f = os.path.join(utils.data_dir, 'badblock.log')
                open(f, 'w').write(str(block.hex_serialize()))
                return False

        if block.number < self.head.number:
            _log.debug("older than head", head_hash=self.head)
            # Q: Should we have any limitations on adding blocks?

        self.index.add_block(block)
        self._store_block(block)

        # set to head if this makes the longest chain w/ most work for that number
        if block.chain_difficulty() > self.head.chain_difficulty():
            _log.debug('new head')
            self._update_head(block)
        elif block.number > self.head.number:
            _log.warn('has higher blk number than head but lower chain_difficulty',
                      head_hash=self.head, block_difficulty=block.chain_difficulty(),
                      head_difficulty=self.head.chain_difficulty())
        self.commit()  # batch commits all changes that came with the new block
        return True

    def get_children(self, block):
        return [self.get(c) for c in self.index.get_children(block.hash)]

    def get_uncles(self, block):
        if not block.has_parent():
            return []
        parent = block.get_parent()
        o = []
        i = 0
        while parent.has_parent() and i < 6:
            grandparent = parent.get_parent()
            o.extend([u for u in self.get_children(grandparent) if u != parent])
            parent = grandparent
            i += 1
        return o

    def add_transaction(self, transaction):
        # _log = log.bind(tx_hash=transaction)
        # _log.debug("add transaction")
        # with self.lock:
        #     res = self.miner.add_transaction(transaction)
        #     if res:
        #         _log.debug("broadcasting valid")
        #         signals.send_local_transactions.send(
        #             sender=None, transactions=[transaction])
        #     return res
        pass

    def get_transactions(self):
        # log.debug("get_transactions called")
        # return self.miner.get_transactions()
        pass

    def get_chain(self, start='', count=10):
        "return 'count' blocks starting from head or start"
        log.debug("get_chain", start=start.encode('hex'), count=count)
        blocks = []
        block = self.head
        if start:
            if start not in self.index.db:
                return []
            block = self.get(start)
            if not self.in_main_branch(block):
                return []
        for i in range(count):
            blocks.append(block)
            if block.is_genesis():
                break
            block = block.get_parent()
        return blocks

    def in_main_branch(self, block):
        try:
            return block.hash == self.index.get_block_by_number(block.number)
        except KeyError:
            return False

    def get_descendants(self, block, count=1):
        log.debug("get_descendants", block_hash=block)
        assert block.hash in self
        block_numbers = range(block.number + 1, min(self.head.number + 1,
                                                    block.number + count + 1))
        return [self.get(self.index.get_block_by_number(n)) for n in block_numbers]
