import pytest
import json
import tempfile
import pyethereum.processblock as processblock
import pyethereum.blocks as blocks
import pyethereum.transactions as transactions

import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()

tempdir = tempfile.mktemp()

def check_testdata(data_keys, expected_keys):
    assert set(data_keys) == set(expected_keys), "test data changed, please adjust tests"


@pytest.fixture(scope="module")
def vm_tests_fixtures():
    """Read vm tests from fixtures"""
    # FIXME: assert that repo is uptodate
    try:
        vm_fixture = json.load(open('fixtures/vmtests.json', 'r'))
    except IOError as e:
        raise IOError("Could not read vmtests.json from fixtures. Make sure you did 'git submodule init'!")
    check_testdata(vm_fixture.keys(),  [u'boolean', u'suicide', u'arith', u'mktx'])
    return vm_fixture

@pytest.mark.xfail # test data not yet valid
def test_boolean():
    do_test_vm('boolean')

@pytest.mark.xfail # test data not yet valid
def test_suicide():
    do_test_vm('suicide')

@pytest.mark.xfail # test data not yet valid
def test_arith():
    do_test_vm('arith')

@pytest.mark.xfail # test data not yet valid
def test_mktx():
    do_test_vm('mktx')


def do_test_vm(name):
    tempdir = tempfile.mktemp()

    logger.debug('running test:%r', name)
    params = vm_tests_fixtures()[name]
    #logger.debug(json.dumps(params, indent=1))
    pre = params['pre']
    execs = params['exec']
    callcreates = params['callcreates']
    env = params['env']
    post = params['post']


    assert set(env.keys()) == set(['code',
                                 'currentGasLimit',
                                 'currentTimestamp',
                                 'previousHash',
                                 'currentCoinbase',
                                 'currentDifficulty',
                                 'currentNumber'])
    # setup env
    blk = blocks.Block(
             prevhash=env['previousHash'].decode('hex'),
             number=int(env['currentNumber']),
             coinbase=env['currentCoinbase'],
             difficulty=int(env['currentDifficulty']),
             gas_limit=int(env['currentGasLimit']),
             timestamp=env['currentTimestamp'])

    # code FIXME WHAT TO DO WITH THIS CODE???
    # if isinstance(env['code'], str):
    #     continue
    # else:
    #     addr = 0 # FIXME
    #     blk.set_code(addr, ''.join(map(chr, env['code'])))

    # setup state
    for address, h in pre.items():
        check_testdata(h.keys(), ['code', 'nonce', 'balance', 'storage'])
        blk.set_balance(address, h['balance'])
        logger.debug('PRE Balance: %r: %r', address, h['balance'])
        blk._set_acct_item(address,'nonce', h['nonce'])
        blk.set_code(address, ''.join(map(chr, h['code'])))
        assert h['storage'] == {} # FOR NOW test contracts don't seem to persist anything

    # execute transactions
    for i, exek in enumerate(execs):
        sender = exek['address']  #  a party that originates a call
        recvaddr = exek['caller']
        tx = transactions.Transaction(nonce=blk._get_acct_item(exek['caller'], 'nonce'),
                                      gasprice=int(exek['gasPrice']),
                                      startgas=int(exek['gas']),
                                      to=recvaddr,
                                      value=int(exek['value']),
                                      data=exek['data'])
        tx.sender = sender
        logger.debug('TX %r > %r v:%r gas:%s @price:%s',
                        sender, recvaddr, tx.value, tx.startgas, tx.gasprice)

        success, output  = processblock.apply_transaction(blk, tx)
        assert success

        # FIXME. check against callcreates

        # data and out not set in tests yet
        assert output == params['out']
        assert not params['out']
        assert not callcreates['data']

    # check state
    for address, h in post.items():
        check_testdata(h.keys(), ['code', 'nonce', 'balance', 'storage'])
        logger.debug('POST: %r %r', address, h['balance'])
        blk.get_balance(address) ==  h['balance']
        blk._get_acct_item(address,'nonce') ==  h['nonce']
        map(ord, blk.get_code(recvaddr)) ==h['code']
        assert storage == {} # FOR NOW test contracts don't seem to persist anything

